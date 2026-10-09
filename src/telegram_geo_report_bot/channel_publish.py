"""Idempotent publication of approved entries to a Telegram channel."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from cryptography.fernet import Fernet, InvalidToken

from .channel_format import format_digest, format_footer, partition_media
from .config import BotConfig
from .entries import EntryRepository
from .exports import publication_blocked
from .operation_lock import OperationLock
from .photos import PhotoError, PhotoStore


STATE_VERSION = 1


class ChannelError(RuntimeError):
    pass


class ChannelStateError(ChannelError):
    pass


class TelegramChannelError(ChannelError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class DigestPreview:
    eligible: int
    photos: int
    initialized: bool
    completed: bool
    completed_at: str | None = None


@dataclass(frozen=True)
class DigestResult:
    sent: int
    photos: int
    photo_fallbacks: int
    completed: bool
    already_completed: bool = False
    empty: bool = False
    resumed: bool = False


def _publishable(entry: dict[str, Any]) -> bool:
    return entry.get("status") == "approved" and not publication_blocked(entry)


def _utc_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def _local_now(now: datetime | None = None) -> datetime:
    value = now or datetime.now().astimezone()
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return value.astimezone()


def _safe_error(body: Any) -> str:
    description = body.get("description") if isinstance(body, dict) else ""
    description = " ".join(str(description).split())
    description = re.sub(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b", "[TOKEN]", description)
    description = re.sub(r"-100\d{5,}", "[CHANNEL]", description)
    description = re.sub(r"https?://\S+", "[URL]", description)
    return description[:240]


def _save_state(path: Path, cipher: Fernet, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as stream:
            stream.write(cipher.encrypt(json.dumps(state, ensure_ascii=False).encode("utf-8")))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def load_state(path: Path, cipher: Fernet, channel_id: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        state = json.loads(cipher.decrypt(path.read_bytes()).decode("utf-8"))
    except (OSError, InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChannelStateError("Kanalzustand ist nicht lesbar") from exc
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        raise ChannelStateError("Kanalzustand hat eine unbekannte Version")
    if str(state.get("channel_id")) != str(channel_id):
        raise ChannelStateError("Kanalzustand gehört zu einem anderen Zielkanal")
    if not all(isinstance(state.get(key), dict) for key in ("published", "batches", "days")):
        raise ChannelStateError("Kanalzustand ist unvollständig")
    return state


def initialize_state(
    repository: EntryRepository,
    state_file: Path,
    cipher: Fernet,
    channel_id: str,
    *,
    now: datetime | None = None,
) -> int:
    if state_file.exists():
        raise ChannelStateError("Kanalzustand ist bereits initialisiert")
    approved = [entry for entry in repository.load() if _publishable(entry)]
    state = {
        "version": STATE_VERSION,
        "channel_id": str(channel_id),
        "initialized_at": _utc_iso(now),
        "published": {
            str(entry["id"]): {"baseline": True, "sent_at": _utc_iso(now)} for entry in approved
        },
        "batches": {},
        "days": {},
    }
    _save_state(state_file, cipher, state)
    return len(approved)


def preview(
    repository: EntryRepository,
    state_file: Path,
    cipher: Fernet,
    channel_id: str,
    *,
    now: datetime | None = None,
) -> DigestPreview:
    state = load_state(state_file, cipher, channel_id)
    entries = [entry for entry in repository.load() if _publishable(entry)]
    if state is None:
        return DigestPreview(len(entries), sum(bool(entry.get("photo")) for entry in entries), False, False)
    eligible = [entry for entry in entries if str(entry.get("id")) not in state["published"]]
    today = _local_now(now).date().isoformat()
    completed = state["days"].get(today) or {}
    return DigestPreview(
        len(eligible),
        sum(bool(entry.get("photo")) for entry in eligible),
        True,
        bool(completed.get("completed_at")),
        completed.get("completed_at"),
    )


class TelegramChannelClient:
    def __init__(self, token: str, channel_id: str, *, timeout: float = 30.0):
        self.channel_id = channel_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.timeout = timeout

    def _call(self, method: str, *, data=None, files=None, attempts: int = 3):
        last_error = None
        for attempt in range(attempts):
            try:
                response = requests.post(
                    f"{self.base_url}/{method}", data=data, files=files, timeout=self.timeout
                )
                body = response.json()
            except (requests.RequestException, ValueError):
                last_error = TelegramChannelError("Telegram ist nicht erreichbar", retryable=True)
            else:
                if response.ok and isinstance(body, dict) and body.get("ok") is True:
                    return body.get("result")
                retryable = response.status_code == 429 or response.status_code >= 500
                description = _safe_error(body)
                last_error = TelegramChannelError(
                    f"Telegram-Kanalvorgang abgelehnt (HTTP {response.status_code})"
                    + (f": {description}" if description else ""),
                    retryable=retryable,
                )
            if attempt < attempts - 1 and last_error.retryable:
                time.sleep(2**attempt)
                continue
            raise last_error
        raise last_error or TelegramChannelError("Telegram-Kanalvorgang fehlgeschlagen")

    def check_channel(self) -> None:
        chat = self._call("getChat", data={"chat_id": self.channel_id})
        if not isinstance(chat, dict):
            raise TelegramChannelError("Telegram hat den Zielkanal nicht bestätigt")
        member = self._call(
            "getChatMember", data={"chat_id": self.channel_id, "user_id": self.bot_id()}
        )
        if not isinstance(member, dict) or member.get("status") not in {"administrator", "creator"}:
            raise TelegramChannelError("Bot besitzt keine Kanalberechtigung")

    def bot_id(self) -> int:
        result = self._call("getMe")
        try:
            return int(result["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TelegramChannelError("Telegram hat keine Bot-ID geliefert") from exc

    def public_bot_url(self) -> str:
        result = self._call("getMe")
        username = result.get("username") if isinstance(result, dict) else None
        return f"https://t.me/{username}" if username else ""

    def send_text(self, text: str) -> int:
        result = self._call("sendMessage", data={"chat_id": self.channel_id, "text": text})
        return int(result["message_id"])

    def send_photo(self, content: bytes) -> int:
        result = self._call(
            "sendPhoto",
            data={"chat_id": self.channel_id},
            files={"photo": ("photo.jpg", BytesIO(content), "image/jpeg")},
        )
        return int(result["message_id"])

    def send_media_group(self, photos: list[bytes]) -> list[int]:
        streams = [BytesIO(value) for value in photos]
        media = [
            {"type": "photo", "media": f"attach://photo{index}"}
            for index in range(len(streams))
        ]
        files = {
            f"photo{index}": (f"photo-{index}.jpg", stream, "image/jpeg")
            for index, stream in enumerate(streams)
        }
        try:
            result = self._call(
                "sendMediaGroup",
                data={"chat_id": self.channel_id, "media": json.dumps(media)},
                files=files,
            )
        finally:
            for stream in streams:
                stream.close()
        if not isinstance(result, list) or len(result) != len(photos):
            raise TelegramChannelError("Telegram hat das Fotoalbum nicht vollständig bestätigt")
        return [int(item["message_id"]) for item in result]


def publish(
    repository: EntryRepository,
    photo_store: PhotoStore,
    state_file: Path,
    lock_path: Path,
    cipher: Fernet,
    config: BotConfig,
    channel_id: str,
    client: TelegramChannelClient,
    *,
    source: str,
    write_authorized: bool,
    daily_limit: bool = True,
    now: datetime | None = None,
) -> DigestResult:
    if source not in {"admin", "scheduled"}:
        raise ValueError("Ungültige Versandquelle")
    if not write_authorized:
        raise ChannelError("Kanal-Schreibzugriff ist nicht freigegeben")
    current = _local_now(now)
    date_key = current.date().isoformat()
    with OperationLock(lock_path, stale_after_seconds=15 * 60):
        state = load_state(state_file, cipher, channel_id)
        if state is None:
            raise ChannelStateError("Kanalversand muss zuerst initialisiert werden")
        if daily_limit and (state["days"].get(date_key) or {}).get("completed_at"):
            return DigestResult(0, 0, 0, True, already_completed=True)
        client.check_channel()
        entries = repository.load()
        entries_by_id = {str(entry["id"]): entry for entry in entries if entry.get("id")}
        open_batches = [batch for batch in state["batches"].values() if not batch.get("completed_at")]
        resumed = bool(open_batches)
        if not open_batches:
            selected = [
                entry for entry in entries
                if _publishable(entry) and str(entry["id"]) not in state["published"]
            ]
            if not selected and source == "admin":
                return DigestResult(0, 0, 0, False, empty=True)
            batch = {
                "created_at": _utc_iso(current),
                "source": source,
                "entry_ids": [str(entry["id"]) for entry in selected],
                "summary_message_ids": [],
                "sent_media_chunks": {},
            }
            state["batches"][date_key] = batch
            _save_state(state_file, cipher, state)
            open_batches = [batch]

        total_sent = total_photos = total_fallbacks = 0
        for batch in open_batches:
            active = [
                entries_by_id[entry_id]
                for entry_id in batch.get("entry_ids", [])
                if entry_id in entries_by_id
                and _publishable(entries_by_id[entry_id])
                and entry_id not in state["published"]
            ]
            if "summary_chunks" not in batch:
                batch["summary_chunks"] = format_digest(active, config, now=current) if active else []
                photo_ids = []
                fallback_ids = []
                for entry in active:
                    entry_id = str(entry["id"])
                    try:
                        photo = photo_store.load(entry.get("photo"))
                    except PhotoError:
                        photo = None
                    if photo is not None:
                        photo_ids.append(entry_id)
                    elif entry.get("photo"):
                        fallback_ids.append(entry_id)
                batch["photo_ids"] = photo_ids
                batch["photo_fallback_ids"] = fallback_ids
                batch["media_chunks"] = partition_media(photo_ids)
                _save_state(state_file, cipher, state)

            summary_ids = batch.setdefault("summary_message_ids", [])
            for text in batch.get("summary_chunks", [])[len(summary_ids) :]:
                summary_ids.append(client.send_text(text))
                _save_state(state_file, cipher, state)
            summary_id = summary_ids[0] if summary_ids else None
            photo_ids = set(batch.get("photo_ids", []))
            fallback_ids = set(batch.get("photo_fallback_ids", []))
            for entry in active:
                entry_id = str(entry["id"])
                if entry_id in photo_ids:
                    continue
                state["published"][entry_id] = {
                    "message_id": summary_id,
                    "sent_at": _utc_iso(),
                    "source": batch.get("source", source),
                    "photo": False,
                    "photo_fallback": entry_id in fallback_ids,
                }
                total_sent += 1
                total_fallbacks += int(entry_id in fallback_ids)
            _save_state(state_file, cipher, state)

            sent_chunks = batch.setdefault("sent_media_chunks", {})
            for index, chunk in enumerate(batch.get("media_chunks", [])):
                key = str(index)
                if key in sent_chunks:
                    continue
                loaded = []
                missing = []
                for entry_id in chunk:
                    entry = entries_by_id.get(entry_id)
                    try:
                        content = photo_store.load(entry.get("photo") if entry else None)
                    except PhotoError:
                        content = None
                    if content is None:
                        missing.append(entry_id)
                    else:
                        loaded.append((entry_id, content))
                message_ids = []
                if len(loaded) == 1:
                    message_ids = [client.send_photo(loaded[0][1])]
                elif loaded:
                    message_ids = client.send_media_group([content for _, content in loaded])
                for (entry_id, _), message_id in zip(loaded, message_ids):
                    state["published"][entry_id] = {
                        "message_id": message_id,
                        "sent_at": _utc_iso(),
                        "source": batch.get("source", source),
                        "photo": True,
                        "photo_fallback": False,
                    }
                    total_sent += 1
                    total_photos += 1
                for entry_id in missing:
                    state["published"][entry_id] = {
                        "message_id": summary_id,
                        "sent_at": _utc_iso(),
                        "source": batch.get("source", source),
                        "photo": False,
                        "photo_fallback": True,
                    }
                    total_sent += 1
                    total_fallbacks += 1
                sent_chunks[key] = message_ids
                _save_state(state_file, cipher, state)

            finished = all(
                entry_id in state["published"] for entry_id in batch.get("entry_ids", [])
            )
            if finished and batch.get("entry_ids") and not batch.get("footer_message_id"):
                footer = format_footer(config, client.public_bot_url())
                if footer:
                    batch["footer_message_id"] = client.send_text(footer)
                    _save_state(state_file, cipher, state)
            if finished:
                batch["completed_at"] = _utc_iso(current)
                if daily_limit:
                    state["days"][date_key] = {
                        "completed_at": batch["completed_at"],
                        "source": batch.get("source", source),
                    }
                _save_state(state_file, cipher, state)

        completed = daily_limit and bool((state["days"].get(date_key) or {}).get("completed_at"))
        return DigestResult(
            total_sent,
            total_photos,
            total_fallbacks,
            completed,
            empty=total_sent == 0,
            resumed=resumed,
        )
