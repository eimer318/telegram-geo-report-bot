"""Small cross-process directory lock."""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from pathlib import Path


class OperationLockedError(RuntimeError):
    pass


class OperationLock:
    def __init__(self, path: Path, *, stale_after_seconds: int = 1800):
        self.path = Path(path)
        self.stale_after_seconds = stale_after_seconds
        self.owner_file = self.path / "owner.json"
        self.token = uuid.uuid4().hex
        self.acquired = False

    def _payload(self):
        return {
            "token": self.token,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "created_at": time.time(),
        }

    def _owner(self):
        try:
            return json.loads(self.owner_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _stale(self, owner):
        if owner.get("host") == socket.gethostname():
            try:
                os.kill(int(owner.get("pid")), 0)
            except (ProcessLookupError, TypeError, ValueError):
                return True
            except (PermissionError, OSError):
                pass
        try:
            return time.time() - float(owner.get("created_at")) > self.stale_after_seconds
        except (TypeError, ValueError):
            return False

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                self.path.mkdir(mode=0o700)
            except FileExistsError as exc:
                owner = self._owner()
                if attempt == 0 and self._stale(owner):
                    try:
                        self.owner_file.unlink(missing_ok=True)
                        self.path.rmdir()
                    except OSError:
                        pass
                    else:
                        continue
                raise OperationLockedError("Der Vorgang läuft bereits") from exc
            self.owner_file.write_text(json.dumps(self._payload()), encoding="utf-8")
            self.acquired = True
            return self
        raise OperationLockedError("Der Vorgang läuft bereits")

    def release(self):
        if self.acquired and self._owner().get("token") == self.token:
            self.owner_file.unlink(missing_ok=True)
            try:
                self.path.rmdir()
            except OSError:
                pass
        self.acquired = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
