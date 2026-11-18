"""Local file storage on the `storage` volume (/data/storage).

Originals: originals/<aa>/<bb>/<uuid>, written once and made read-only (originals are
immutable). Uploads are first written to .incoming/ on the SAME volume and moved with an atomic
rename, so a crash never leaves a half-written original. Orphaned incoming files (crash during
an upload) are removed when older than an hour.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

INCOMING = ".incoming"
ORIGINALS = "originals"


class LocalStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def incoming_path(self) -> Path:
        directory = self.root / INCOMING
        directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        return directory / f"{uuid.uuid4().hex}.part"

    def original_path(self, key: str) -> Path:
        uuid.UUID(hex=key)  # the key is always a generated UUID, never user input
        return self.root / ORIGINALS / key[:2] / key[2:4] / key

    def store(self, incoming: Path) -> str:
        """Move an incoming file to its final, read-only place; return the storage key."""
        key = uuid.uuid4().hex
        target = self.original_path(key)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        os.chmod(incoming, 0o440)
        os.replace(incoming, target)  # atomic on the same file system
        return key

    def cleanup_incoming(self, older_than_seconds: int = 3600) -> int:
        directory = self.root / INCOMING
        if not directory.is_dir():
            return 0
        limit = time.time() - older_than_seconds
        removed = 0
        for item in directory.iterdir():
            try:
                if item.is_file() and item.stat().st_mtime < limit:
                    item.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
        return removed


def get_storage(state: object) -> LocalStorage:
    """The storage of this application instance (created on first use, kept on app.state)."""
    storage = getattr(state, "documents_storage", None)
    if storage is None:
        storage = LocalStorage(state.settings.storage_dir)  # type: ignore[attr-defined]
        state.documents_storage = storage  # type: ignore[attr-defined]
    return storage
