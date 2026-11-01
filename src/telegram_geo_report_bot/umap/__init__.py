"""Optional, fail-safe synchronization of approved reports with uMap."""

from .sync import SyncResult, synchronize

__all__ = ["SyncResult", "synchronize"]
