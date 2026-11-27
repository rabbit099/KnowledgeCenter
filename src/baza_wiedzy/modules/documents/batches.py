"""Results of one upload form with several files, and files held as possible duplicates
(ADR-032).

A batch is a small JSON file on the storage volume (`.batches/<id>.json`) with one row per
file: its sanitised name, the outcome and, for a held duplicate, the file itself next to it
(`.batches/<id>/<row>.part`, same volume, so "upload anyway" is an atomic rename into
originals/). A batch belongs to the user who uploaded it; anybody else gets "not found". It
lives UPLOAD_BATCH_HOURS; held files that nobody decided about are removed with it.

Row outcomes:
  uploaded   a document was created (document_id)
  held       identical content exists in a document the user can see: waits for
             "Wgraj mimo to" (-> uploaded) or "Pomiń" (-> skipped)
  skipped    the user chose "Pomiń"
  rejected   refused (reason: a message id of the form error it would have had alone)
  expired    a held file whose batch outlived its time (shown only if the JSON survived)

File names live only here and in the database, never in logs (CLAUDE.md 8).
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BATCHES = ".batches"

UPLOADED = "uploaded"
HELD = "held"
SKIPPED = "skipped"
REJECTED = "rejected"


def _key(batch_id: str) -> str:
    return uuid.UUID(hex=batch_id).hex  # never user input in a path: ValueError otherwise


class BatchStore:
    def __init__(self, storage_root: str | Path) -> None:
        self.root = Path(storage_root) / BATCHES

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def _json(self, batch_id: str) -> Path:
        return self.root / f"{_key(batch_id)}.json"

    def held_path(self, batch_id: str, row: int) -> Path:
        directory = self.root / _key(batch_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        return directory / f"{int(row)}.part"

    def save(self, batch_id: str, user_id: uuid.UUID, rows: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o750)
        data = {"user_id": str(user_id), "created": time.time(), "rows": rows}
        target = self._json(batch_id)
        partial = target.with_suffix(".tmp")
        partial.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(partial, target)

    def load(self, batch_id: str, user_id: uuid.UUID) -> list[dict[str, Any]] | None:
        """The rows, or None when the batch does not exist or belongs to someone else."""
        try:
            path = self._json(batch_id)
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        if data.get("user_id") != str(user_id):
            return None
        rows: list[dict[str, Any]] = data["rows"]
        return rows

    @contextmanager
    def locked(
        self, batch_id: str, user_id: uuid.UUID
    ) -> Iterator[tuple[list[dict[str, Any]], Callable[[], None]] | None]:
        """Exclusive access to a batch for a decision: (rows, save) or None (not found)."""
        try:
            path = self._json(batch_id)
            handle = path.open("r+", encoding="utf-8")
        except (ValueError, OSError):
            yield None
            return
        with handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            data = json.loads(handle.read())
            if data.get("user_id") != str(user_id):
                yield None
                return

            def save() -> None:
                handle.seek(0)
                handle.truncate()
                handle.write(json.dumps(data, ensure_ascii=False))
                handle.flush()

            yield data["rows"], save

    def cleanup(self, older_than_seconds: int) -> int:
        """Remove batches (and their held files) older than the limit; returns how many."""
        if not self.root.is_dir():
            return 0
        limit = time.time() - older_than_seconds
        removed = 0
        for item in self.root.iterdir():
            try:
                if item.stat().st_mtime >= limit:
                    continue
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink()
                    removed += item.suffix == ".json"
            except FileNotFoundError:
                continue
        return removed
