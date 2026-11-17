"""Upload: copy to .incoming/ while hashing and counting, check the type by content, move the
file to its final place, create the draft document and its first version, audit.

The request body is parsed by Starlette (files above 1 MB are spooled to a temporary file, never
held in memory); this module streams that spool to the storage volume in chunks.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from baza_wiedzy.core.audit.service import Actor, record
from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.modules.documents import filenames, filetypes
from baza_wiedzy.modules.documents.filetypes import UnsupportedFile
from baza_wiedzy.modules.documents.models import Document, Version
from baza_wiedzy.modules.documents.storage import LocalStorage

log = logging.getLogger(__name__)
CHUNK = 1024 * 1024
MAX_TITLE = 300


class UploadTooLarge(UnsupportedFile):
    def __init__(self) -> None:
        super().__init__(N_("The file is larger than the allowed size."))


@dataclass(frozen=True)
class Received:
    path: Path
    size: int
    sha256: str


async def receive(chunks: AsyncIterator[bytes], storage: LocalStorage, max_bytes: int) -> Received:
    """Write the chunks to .incoming/, computing SHA-256; stop as soon as the limit is passed."""
    path = storage.incoming_path()
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("xb") as target:
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLarge()
                digest.update(chunk)
                target.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return Received(path=path, size=size, sha256=digest.hexdigest())


@dataclass(frozen=True)
class Uploaded:
    document: Document
    version: Version


def create(
    db: Session,
    storage: LocalStorage,
    received: Received,
    *,
    actor: Actor,
    raw_filename: str,
    title: str,
    ip: str | None,
) -> Uploaded:
    """Validate by content, store and create the rows (the caller commits)."""
    filename = filenames.sanitize(raw_filename)
    try:
        kind = filetypes.detect(received.path, filename)
    except UnsupportedFile:
        received.path.unlink(missing_ok=True)
        raise
    if received.size == 0:
        received.path.unlink(missing_ok=True)
        raise UnsupportedFile(N_("The file is empty."))
    assert actor.user_id is not None
    key = storage.store(received.path)
    typed = " ".join(title.split())[:MAX_TITLE]
    # Case is never changed: the title is what was typed, else the file name without extension
    # (for e-mails processing replaces it with the subject, see processing.apply_subject_title).
    document = Document(
        title=typed or filename.rsplit(".", 1)[0] or filename,
        title_source="user" if typed else "filename",
        owner_id=actor.user_id,
    )
    db.add(document)
    db.flush()
    version = Version(
        id=uuid.uuid4(),
        document_id=document.id,
        version_no=1,
        kind=kind,
        original_filename=filename,
        size_bytes=received.size,
        sha256=received.sha256,
        storage_key=key,
        uploaded_by=actor.user_id,
    )
    db.add(version)
    db.flush()
    document.latest_version_id = version.id
    # Only identifiers and the hash: no file name, no content (CLAUDE.md 8, GDPR).
    record(
        db,
        "document.upload",
        actor,
        entity_type="document",
        entity_id=document.id,
        ip=ip,
        details={
            "version_id": str(version.id),
            "sha256": received.sha256,
            "size_bytes": received.size,
            "kind": kind,
        },
    )
    log.info("document uploaded: document=%s version=%s kind=%s", document.id, version.id, kind)
    return Uploaded(document=document, version=version)
