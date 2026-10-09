"""Encrypted administrator identifiers."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .operation_lock import OperationLock


class AdminStoreError(RuntimeError):
    pass


_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    resolved = Path(path).resolve()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(resolved, threading.RLock())


def admin_hash(user_id: int) -> str:
    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()


class AdminStore:
    def __init__(self, path: Path, cipher: Fernet):
        self.path = Path(path)
        self.cipher = cipher
        self._lock = _lock_for(self.path)
        self._process_lock_path = self.path.with_name(f"{self.path.name}.lock")

    def load(self) -> set[str]:
        with self._lock:
            return self._load_unlocked()

    def _load_unlocked(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            value = self.cipher.decrypt(self.path.read_bytes()).decode("utf-8")
        except (OSError, InvalidToken, UnicodeDecodeError) as exc:
            raise AdminStoreError("Adminspeicher ist nicht lesbar") from exc
        return {item.strip() for item in value.split(",") if item.strip()}

    def save(self, values: set[str]) -> None:
        with self._lock:
            with OperationLock(self._process_lock_path, stale_after_seconds=120):
                self._save_unlocked(values)

    def _save_unlocked(self, values: set[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with temporary.open("wb") as stream:
                stream.write(self.cipher.encrypt(",".join(sorted(values)).encode("utf-8")))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        finally:
            temporary.unlink(missing_ok=True)

    def contains(self, user_id: int) -> bool:
        return admin_hash(user_id) in self.load()

    def add(self, user_id: int) -> bool:
        with self._lock:
            with OperationLock(self._process_lock_path, stale_after_seconds=120):
                values = self._load_unlocked()
                value = admin_hash(user_id)
                if value in values:
                    return False
                values.add(value)
                self._save_unlocked(values)
                return True

    def remove(self, user_id: int) -> bool:
        with self._lock:
            with OperationLock(self._process_lock_path, stale_after_seconds=120):
                values = self._load_unlocked()
                value = admin_hash(user_id)
                if value not in values:
                    return False
                values.remove(value)
                self._save_unlocked(values)
                return True

    def remove_preserving_one(self, user_id: int) -> bool:
        """Remove an administrator without allowing the store to become empty."""
        with self._lock:
            with OperationLock(self._process_lock_path, stale_after_seconds=120):
                values = self._load_unlocked()
                value = admin_hash(user_id)
                if value not in values:
                    return False
                if len(values) <= 1:
                    raise AdminStoreError("Der letzte Administrator kann nicht entfernt werden")
                values.remove(value)
                self._save_unlocked(values)
                return True
