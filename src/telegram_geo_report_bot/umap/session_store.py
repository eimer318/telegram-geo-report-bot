"""Encrypted uMap session storage."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class UMapSessionError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class UMapCredentials:
    session_id: str
    csrf_token: str
    user_agent: str


def save_session(path: Path, cipher: Fernet, credentials: UMapCredentials) -> Path:
    if not all((credentials.session_id, credentials.csrf_token, credentials.user_agent)):
        raise UMapSessionError("Die uMap-Sitzung ist unvollständig")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "session_id": credentials.session_id,
        "csrf_token": credentials.csrf_token,
        "user_agent": credentials.user_agent,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as stream:
            stream.write(cipher.encrypt(json.dumps(payload).encode("utf-8")))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)
    return path


def load_session(path: Path, cipher: Fernet) -> UMapCredentials:
    try:
        raw = cipher.decrypt(Path(path).read_bytes())
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UMapSessionError("Die verschlüsselte uMap-Sitzung ist nicht lesbar") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise UMapSessionError("Nicht unterstützte uMap-Sitzungsversion")
    credentials = UMapCredentials(
        str(payload.get("session_id") or "").strip(),
        str(payload.get("csrf_token") or "").strip(),
        str(payload.get("user_agent") or "").strip(),
    )
    if not all((credentials.session_id, credentials.csrf_token, credentials.user_agent)):
        raise UMapSessionError("Die verschlüsselte uMap-Sitzung ist unvollständig")
    return credentials
