"""Encrypted line-based entry storage and review operations."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken

from .exports import publication_blocked, write_exports
from .operation_lock import OperationLock
from .security import assess_description


VALID_STATUSES = {"pending", "approved", "rejected"}
AUTO_VALIDATION_REFERENCE_STATUSES = {"pending", "approved"}
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class EntryError(RuntimeError):
    pass


class EntryLoadError(EntryError):
    def __init__(self, path: Path, errors: list[tuple[Any, str]]):
        self.path = Path(path)
        self.errors = tuple(errors)
        locations = ", ".join(str(location) for location, _ in errors)
        super().__init__(f"{self.path}: unlesbare Eintragszeile(n): {locations}")


class EntrySecurityError(EntryError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lock_for(path: Path) -> threading.RLock:
    resolved = Path(path).resolve()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(resolved, threading.RLock())


def is_automatic_approval_blocked(entry: dict[str, Any]) -> bool:
    security = entry.get("security")
    return isinstance(security, dict) and security.get("automatic_approval_blocked") is True


def distance_meters(lat1: Any, lon1: Any, lat2: Any, lon2: Any) -> float:
    lat1, lon1, lat2, lon2 = map(float, (lat1, lon1, lat2, lon2))
    if not (-90 <= lat1 <= 90 and -90 <= lat2 <= 90):
        raise ValueError("Breitengrad außerhalb des gültigen Bereichs")
    if not (-180 <= lon1 <= 180 and -180 <= lon2 <= 180):
        raise ValueError("Längengrad außerhalb des gültigen Bereichs")
    radius = 6_371_000
    delta_lat = radians(lat2 - lat1)
    delta_lon = radians(lon2 - lon1)
    value = (
        sin(delta_lat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(delta_lon / 2) ** 2
    )
    return radius * 2 * atan2(sqrt(value), sqrt(1 - value))


def count_reporter_entries_on_date(
    entries: list[dict[str, Any]],
    reporter_hash: str,
    target_date: date | None = None,
    tz=None,
) -> int:
    if not reporter_hash:
        return 0
    tz = tz or datetime.now().astimezone().tzinfo
    target_date = target_date or datetime.now(tz).date()
    count = 0
    for entry in entries:
        if entry.get("reporter_hash") != reporter_hash:
            continue
        parsed = _entry_timestamp(entry)
        if parsed is not None and parsed.astimezone(tz).date() == target_date:
            count += 1
    return count


def _entry_timestamp(entry: dict[str, Any]) -> datetime | None:
    raw = entry.get("timestamp") or entry.get("created_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _reporter_identity(entry: dict[str, Any]) -> Any:
    return entry.get("reporter_hash") or entry.get("reporter_id") or entry.get("user_id")


def _iter_nonempty_lines_reverse(path: Path, block_size: int = 64 * 1024):
    with Path(path).open("rb") as source:
        source.seek(0, os.SEEK_END)
        position = source.tell()
        remainder = b""
        while position > 0:
            size = min(block_size, position)
            position -= size
            source.seek(position)
            remainder = source.read(size) + remainder
            lines = remainder.split(b"\n")
            remainder = lines[0]
            for line in reversed(lines[1:]):
                if line.strip():
                    yield line.strip()
        if remainder.strip():
            yield remainder.strip()


def validate_entry(
    entry: dict[str, Any], comparison_entries: list[dict[str, Any]], max_distance_m: int = 100
) -> dict[str, Any]:
    if publication_blocked(entry) or is_automatic_approval_blocked(entry):
        return {"approved": False, "reference": None, "distance": None}
    reporter = _reporter_identity(entry)
    if reporter is None:
        return {"approved": False, "reference": None, "distance": None}
    candidates = []
    for other in comparison_entries:
        if other is entry or (entry.get("id") and other.get("id") == entry.get("id")):
            continue
        if other.get("status", "pending") not in AUTO_VALIDATION_REFERENCE_STATUSES:
            continue
        if publication_blocked(other) or is_automatic_approval_blocked(other):
            continue
        if (other.get("category"), other.get("subcategory")) != (
            entry.get("category"),
            entry.get("subcategory"),
        ):
            continue
        other_reporter = _reporter_identity(other)
        if other_reporter is None or other_reporter == reporter:
            continue
        try:
            distance = distance_meters(
                entry.get("lat"), entry.get("lon"), other.get("lat"), other.get("lon")
            )
        except (TypeError, ValueError):
            continue
        if distance <= max_distance_m:
            candidates.append((distance, other))
    if not candidates:
        return {"approved": False, "reference": None, "distance": None}
    distance, reference = min(candidates, key=lambda value: (value[0], str(value[1].get("id"))))
    return {"approved": True, "reference": reference.get("id"), "distance": round(distance, 1)}


class EntryRepository:
    """Single authority for encrypted data and derived exports."""

    def __init__(
        self,
        entries_file: Path,
        json_export_file: Path,
        geojson_export_file: Path,
        cipher: Fernet,
    ):
        self.entries_file = Path(entries_file)
        self.json_export_file = Path(json_export_file)
        self.geojson_export_file = Path(geojson_export_file)
        self.cipher = cipher
        self._lock = _lock_for(self.entries_file)
        self._process_lock_path = self.entries_file.with_name(
            f"{self.entries_file.name}.lock"
        )

    def _process_lock(self) -> OperationLock:
        return OperationLock(self._process_lock_path, stale_after_seconds=120)

    def load(self, *, strict: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            return self._load_unlocked(strict=strict)

    def _load_unlocked(self, *, strict: bool = True) -> list[dict[str, Any]]:
        if not self.entries_file.exists():
            return []
        entries: list[dict[str, Any]] = []
        errors: list[tuple[Any, str]] = []
        with self.entries_file.open("rb") as stream:
            for line_number, encrypted in enumerate(stream, start=1):
                encrypted = encrypted.strip()
                if not encrypted:
                    continue
                try:
                    value = json.loads(self.cipher.decrypt(encrypted).decode("utf-8"))
                    if not isinstance(value, dict):
                        raise TypeError("NotAnObject")
                    entries.append(value)
                except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
                    errors.append((line_number, type(exc).__name__))
        if errors:
            if strict:
                raise EntryLoadError(self.entries_file, errors)
            logging.error("%s unlesbare Eintragszeile(n)", len(errors))
        return entries

    def page(self, *, offset: int = 0, limit: int = 50) -> tuple[list[dict[str, Any]], bool]:
        if offset < 0 or limit <= 0:
            raise ValueError("Ungültiger Seitenbereich")
        if not self.entries_file.exists():
            return [], False
        entries: list[dict[str, Any]] = []
        errors: list[tuple[Any, str]] = []
        seen = 0
        has_more = False
        with self._lock:
            for encrypted in _iter_nonempty_lines_reverse(self.entries_file):
                if seen < offset:
                    seen += 1
                    continue
                if len(entries) >= limit:
                    has_more = True
                    break
                position_from_end = seen + 1
                seen += 1
                try:
                    value = json.loads(self.cipher.decrypt(encrypted).decode("utf-8"))
                    if not isinstance(value, dict):
                        raise TypeError("NotAnObject")
                    entries.append(value)
                except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
                    errors.append((f"vom Ende {position_from_end}", type(exc).__name__))
        if errors:
            raise EntryLoadError(self.entries_file, errors)
        return entries, has_more

    def save(self, entries: list[dict[str, Any]]) -> None:
        with self._lock:
            with self._process_lock():
                self._save_unlocked(entries)

    def _save_unlocked(self, entries: list[dict[str, Any]]) -> None:
        if not all(isinstance(entry, dict) for entry in entries):
            raise ValueError("Alle Einträge müssen JSON-Objekte sein")
        identifiers = [str(entry.get("id") or "") for entry in entries]
        if any(not value for value in identifiers) or len(identifiers) != len(set(identifiers)):
            raise ValueError("Jeder Eintrag benötigt eine eindeutige UUID")
        self.entries_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.entries_file.name}.", suffix=".tmp", dir=self.entries_file.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with temporary.open("wb") as stream:
                for entry in entries:
                    serialized = json.dumps(entry, ensure_ascii=False).encode("utf-8")
                    stream.write(self.cipher.encrypt(serialized) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.entries_file)
            try:
                self.entries_file.chmod(0o600)
            except OSError:
                pass
            write_exports(entries, self.json_export_file, self.geojson_export_file)
        finally:
            temporary.unlink(missing_ok=True)

    def refresh_exports(self) -> int:
        with self._lock:
            with self._process_lock():
                return write_exports(
                    self._load_unlocked(), self.json_export_file, self.geojson_export_file
                )

    def mutate(self, callback: Callable[[list[dict[str, Any]]], Any]) -> Any:
        with self._lock:
            with self._process_lock():
                entries = self._load_unlocked()
                result = callback(entries)
                self._save_unlocked(entries)
                return result

    def append(self, entry: dict[str, Any]) -> bool:
        def operation(entries):
            if any(existing.get("id") == entry.get("id") for existing in entries):
                return False
            entries.append(entry)
            return True

        return bool(self.mutate(operation))

    def pending(self) -> list[dict[str, Any]]:
        return [entry for entry in self.load() if entry.get("status", "pending") == "pending"]

    def set_status(self, entry_id: str, status: str, reviewed_by: str | None = None) -> bool:
        if status not in VALID_STATUSES:
            raise ValueError("Ungültiger Status")

        def operation(entries):
            for entry in entries:
                if entry.get("id") != entry_id:
                    continue
                if status == "approved" and publication_blocked(entry):
                    raise EntrySecurityError("Der Eintrag muss vor der Freigabe bereinigt werden")
                previous = entry.get("status", "pending")
                if previous == status:
                    return False
                now = utc_now_iso()
                entry["status"] = status
                entry["validation"] = None if status == "pending" else {
                    "type": "manual",
                    "decision": status,
                    "validated_at": now,
                }
                review = entry.setdefault("review", {})
                review.update(
                    {"reviewed": status != "pending", "reviewed_by": reviewed_by, "reviewed_at": now}
                )
                review.setdefault("history", []).append(
                    {
                        "changed_at": now,
                        "changed_by": reviewed_by,
                        "previous_status": previous,
                        "status": status,
                    }
                )
                return True
            return False

        return bool(self.mutate(operation))

    def replace_blocked_description(
        self,
        entry_id: str,
        description: str,
        reviewed_by: str | None = None,
        *,
        max_length: int = 60,
    ) -> bool:
        assessment = assess_description(description, max_length=max_length)
        if assessment.too_long or assessment.suspicious:
            raise EntrySecurityError("Die Ersatzbeschreibung ist nicht zulässig")

        def operation(entries):
            for entry in entries:
                if entry.get("id") != entry_id:
                    continue
                security = entry.get("security")
                if not isinstance(security, dict) or not security.get("publication_blocked"):
                    raise EntrySecurityError("Keine aktive Sicherheitssperre")
                now = utc_now_iso()
                entry["description"] = assessment.normalized or "Keine Zusatzinfos"
                entry["status"] = "pending"
                entry["validation"] = None
                security.update(
                    {
                        "publication_blocked": False,
                        "automatic_approval_blocked": True,
                        "cleared_at": now,
                        "cleared_by": reviewed_by,
                    }
                )
                return True
            return False

        return bool(self.mutate(operation))

    def delete_status(self, status: str) -> list[dict[str, Any]]:
        if status not in VALID_STATUSES:
            raise ValueError("Ungültiger Status")

        def operation(entries):
            removed = [entry for entry in entries if entry.get("status", "pending") == status]
            entries[:] = [entry for entry in entries if entry.get("status", "pending") != status]
            return removed

        return list(self.mutate(operation))

    def auto_validate(
        self, *, now: datetime | None = None, lookback_hours: int = 168, max_distance_m: int = 100
    ) -> list[str]:
        if lookback_hours <= 0:
            raise ValueError("lookback_hours muss positiv sein")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        cutoff = current - timedelta(hours=lookback_hours)

        def operation(entries):
            recent = [
                entry
                for entry in entries
                if (timestamp := _entry_timestamp(entry)) is not None
                and cutoff <= timestamp <= current
            ]
            decisions = []
            for entry in recent:
                if entry.get("status", "pending") != "pending":
                    continue
                result = validate_entry(entry, recent, max_distance_m=max_distance_m)
                if result["approved"]:
                    decisions.append((entry, result))
            for entry, result in decisions:
                entry["status"] = "approved"
                entry["validation"] = {
                    "type": "automatic",
                    "reference_entry_id": result["reference"],
                    "distance_m": result["distance"],
                    "validated_at": current.isoformat(),
                }
            return [str(entry.get("id")) for entry, _ in decisions]

        return list(self.mutate(operation))
