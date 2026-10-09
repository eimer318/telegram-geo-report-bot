"""Input validation, encrypted rate limits, and pseudonymous security events."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .operation_lock import OperationLock


DESCRIPTION_MAX_LENGTH = 60
ADDRESS_MAX_LENGTH = 200
_COMMAND_WORDS = (
    "id", "whoami", "uname", "cat", "curl", "wget", "sh", "bash",
    "python", "perl", "php", "powershell", "cmd", "nc", "netcat",
)
_COMMAND_AFTER_SEPARATOR_RE = re.compile(
    r"(?:^|[;|&])\s*(?:" + "|".join(_COMMAND_WORDS) + r")(?:\s|$)", re.IGNORECASE
)
_HTML_TAG_RE = re.compile(r"<\s*/?\s*[a-z][^>]*>", re.IGNORECASE)
_HTML_EVENT_RE = re.compile(r"\bon[a-z]+\s*=", re.IGNORECASE)
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class SecurityStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class TextAssessment:
    normalized: str
    reason_codes: tuple[str, ...]
    too_long: bool = False

    @property
    def suspicious(self) -> bool:
        return bool(self.reason_codes)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int = 0


def assess_text(value: Any, *, max_length: int) -> TextAssessment:
    raw = str(value or "")
    reasons = []
    if any(char not in "\r\n\t" and unicodedata.category(char) in {"Cc", "Cf"} for char in raw):
        reasons.append("unicode_control_character")
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", raw)).strip()
    lowered = normalized.casefold()
    checks = (
        ("shell_command_substitution", "$(" in normalized),
        ("shell_backtick", "`" in normalized),
        ("shell_parameter_expansion", "${" in normalized),
        ("shell_command_chaining", "&&" in normalized or "||" in normalized),
        ("shell_command_separator", bool(_COMMAND_AFTER_SEPARATOR_RE.search(normalized))),
        ("html_markup", bool(_HTML_TAG_RE.search(normalized)) or "<!--" in normalized),
        ("html_active_content", "javascript:" in lowered or bool(_HTML_EVENT_RE.search(normalized))),
        ("template_expression", any(marker in normalized for marker in ("{{", "}}", "{%", "%}", "<%", "%>"))),
    )
    reasons.extend(code for code, matched in checks if matched)
    return TextAssessment(
        normalized=normalized,
        reason_codes=tuple(dict.fromkeys(reasons)),
        too_long=len(normalized) > int(max_length),
    )


def assess_description(value: Any, max_length: int = DESCRIPTION_MAX_LENGTH) -> TextAssessment:
    return assess_text(value, max_length=max_length)


def assess_address_query(value: Any) -> TextAssessment:
    return assess_text(value, max_length=ADDRESS_MAX_LENGTH)


def pseudonymize_user(user_id: int, fernet_key: str) -> str:
    try:
        raw_key = base64.urlsafe_b64decode(fernet_key)
    except (ValueError, TypeError) as exc:
        raise SecurityStateError("Ungültiger Schlüssel für Pseudonymisierung") from exc
    separated = hashlib.sha256(b"telegram-geo-report-bot/reporter/v1\0" + raw_key).digest()
    return hmac.new(separated, str(user_id).encode("utf-8"), hashlib.sha256).hexdigest()


def _lock_for(path: Path) -> threading.RLock:
    resolved = Path(path).resolve()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(resolved, threading.RLock())


def _load_encrypted(path: Path, cipher: Fernet, *, default: Any) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    try:
        raw = cipher.decrypt(path.read_bytes()).decode("utf-8")
        return json.loads(raw)
    except (OSError, InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecurityStateError(f"Verschlüsselter Sicherheitszustand ist unlesbar: {path.name}") from exc


def _save_encrypted(path: Path, cipher: Fernet, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as stream:
            stream.write(cipher.encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def record_security_event(
    path: Path,
    cipher: Fernet,
    reporter_hash: str,
    event_type: str,
    reasons: tuple[str, ...] | list[str] = (),
    *,
    entry_id: str | None = None,
    now: datetime | None = None,
) -> None:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = current - timedelta(days=30)
    with _lock_for(path):
        process_lock = Path(path).with_name(f"{Path(path).name}.lock")
        with OperationLock(process_lock, stale_after_seconds=120):
            payload = _load_encrypted(path, cipher, default={"version": 1, "events": []})
            if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
                raise SecurityStateError("Ungültiger Sicherheitsereignis-Zustand")
            retained = []
            for event in payload["events"]:
                try:
                    timestamp = datetime.fromisoformat(
                        str(event.get("at", "")).replace("Z", "+00:00")
                    )
                except (TypeError, ValueError):
                    continue
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                if timestamp >= cutoff:
                    retained.append(event)
            retained.append(
                {
                    "at": current.isoformat(),
                    "reporter": reporter_hash,
                    "type": str(event_type),
                    "reasons": sorted({str(reason) for reason in reasons}),
                    "entry_id": str(entry_id) if entry_id else None,
                }
            )
            payload["events"] = retained[-10_000:]
            _save_encrypted(path, cipher, payload)


def security_event_summary(
    path: Path, cipher: Fernet, *, now: datetime | None = None
) -> dict[str, Any]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = current - timedelta(hours=24)
    with _lock_for(path):
        payload = _load_encrypted(path, cipher, default={"version": 1, "events": []})
    events = []
    for event in payload.get("events", []):
        try:
            timestamp = datetime.fromisoformat(str(event.get("at", "")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if timestamp >= cutoff:
            events.append(event)
    reporters: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for event in events:
        reporter = str(event.get("reporter") or "")
        if reporter:
            reporters[reporter] = reporters.get(reporter, 0) + 1
        for reason in event.get("reasons") or []:
            reasons[str(reason)] = reasons.get(str(reason), 0) + 1
    return {
        "events": len(events),
        "reporters": len(reporters),
        "repeat_reporters": sum(count > 1 for count in reporters.values()),
        "reasons": dict(sorted(reasons.items())),
    }


def check_and_record_rate_action(
    path: Path,
    cipher: Fernet,
    reporter_hash: str,
    *,
    now: datetime | None = None,
    minute_limit: int = 3,
    hour_limit: int = 10,
) -> RateLimitResult:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    hour_cutoff = current - timedelta(hours=1)
    minute_cutoff = current - timedelta(minutes=1)
    with _lock_for(path):
        process_lock = Path(path).with_name(f"{Path(path).name}.lock")
        with OperationLock(process_lock, stale_after_seconds=120):
            payload = _load_encrypted(path, cipher, default={"version": 1, "reporters": {}})
            reporters = payload.get("reporters")
            if not isinstance(reporters, dict):
                raise SecurityStateError("Ungültiger Ratenlimit-Zustand")
            timestamps = []
            for raw in reporters.get(reporter_hash, []):
                try:
                    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                parsed = parsed.astimezone(timezone.utc)
                if parsed >= hour_cutoff:
                    timestamps.append(parsed)
            recent_minute = [value for value in timestamps if value >= minute_cutoff]
            retry_after = 0
            if len(recent_minute) >= minute_limit:
                retry_after = max(
                    1,
                    int(
                        (recent_minute[0] + timedelta(minutes=1) - current).total_seconds()
                    )
                    + 1,
                )
            elif len(timestamps) >= hour_limit:
                retry_after = max(
                    1,
                    int((timestamps[0] + timedelta(hours=1) - current).total_seconds())
                    + 1,
                )
            if retry_after:
                reporters[reporter_hash] = [value.isoformat() for value in timestamps]
                _save_encrypted(path, cipher, payload)
                return RateLimitResult(False, retry_after)
            timestamps.append(current)
            reporters[reporter_hash] = [value.isoformat() for value in timestamps]
            _save_encrypted(path, cipher, payload)
            return RateLimitResult(True, 0)
