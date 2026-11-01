"""Safe uMap synchronization orchestration."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..operation_lock import OperationLock
from .client import UMapClient
from .merge import MergeSummary, merge_entries, validate_layer
from .photos import photo_data_uri
from .session_store import load_session


class UMapSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class SyncResult:
    summary: MergeSummary
    applied: bool
    verified: bool
    backup_file: Path | None = None


def _require_config(services):
    config = services.settings.config.umap
    if config is None:
        raise UMapSyncError("Die uMap-Anbindung ist nicht konfiguriert oder deaktiviert")
    return config


def build_client(services, *, session=None) -> UMapClient:
    config = _require_config(services)
    credentials = load_session(
        services.settings.paths.umap_session_file,
        services.settings.cipher,
    )
    return UMapClient(
        config,
        credentials,
        user_agent=services.settings.umap_user_agent,
        session=session,
    )


def check_connection(services, client=None) -> bool:
    config = _require_config(services)
    client = client or build_client(services)
    client.verify_login()
    snapshot = client.download_layer()
    validate_layer(snapshot.layer, config.layer_id)
    return True


def _encrypted_backup(layer, directory: Path, cipher) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = directory / f"layer_{stamp}.geojson.enc"
    raw = json.dumps(layer, ensure_ascii=False, indent=2).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=directory
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as stream:
            stream.write(cipher.encrypt(raw))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        try:
            target.chmod(0o600)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)
    return target


def synchronize(services, client=None, *, apply: bool = False, photo_loader=None) -> SyncResult:
    config = _require_config(services)
    client = client or build_client(services)
    photo_loader = photo_loader or (
        lambda name: photo_data_uri(services.photos, name)
    )
    with OperationLock(services.settings.paths.umap_lock_path):
        entries = services.entries.load()
        client.verify_login()
        before = client.download_layer()
        merged = merge_entries(
            before.layer,
            entries,
            services.settings.config,
            config,
            photo_loader,
        )
        if not apply:
            return SyncResult(merged.summary, applied=False, verified=False)
        if not services.settings.umap_write_enabled:
            raise UMapSyncError(
                "Schreibzugriff erfordert --apply und UMAP_ALLOW_WRITE=true"
            )
        if not merged.summary.changed:
            return SyncResult(merged.summary, applied=False, verified=True)

        backup = _encrypted_backup(
            before.layer,
            services.settings.paths.umap_backup_dir,
            services.settings.cipher,
        )
        client.upload_layer(merged.layer, before.reference)
        after = client.download_layer()
        verification = merge_entries(
            after.layer,
            entries,
            services.settings.config,
            config,
            photo_loader,
        )
        if verification.summary.changed:
            raise UMapSyncError(
                "Der uMap-Serverzustand weicht nach dem Upload vom erwarteten Ergebnis ab"
            )
        return SyncResult(
            merged.summary,
            applied=True,
            verified=True,
            backup_file=backup,
        )
