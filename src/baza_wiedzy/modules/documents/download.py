"""Downloading the original file of a document (Phase 3, ADR-025).

Who: everyone who can see the document (visibility_clause); the file is the current version
(`Document.current_version_id`: the published one for published and archived documents, the
latest for drafts). Every download is audited as document.download with identifiers only.

How the response is made safe:
- always `application/octet-stream` and `Content-Disposition: attachment`: the browser saves the
  file and never renders it in our origin (no HTML, SVG or PDF viewer in our domain);
- `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`
  (defence in depth if a browser displays it anyway), `Cache-Control: private, no-store`;
- the file name: the sanitised name from the upload, with the extension of the detected type
  appended when it does not match (a renamed file is saved with the right extension); sent as
  an ASCII fallback plus the exact UTF-8 name (RFC 6266 / RFC 5987);
- no Range support: the whole file, with Content-Length.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from baza_wiedzy.core.audit.service import Actor, record
from baza_wiedzy.core.permissions.service import CurrentUser
from baza_wiedzy.modules.documents.filenames import FALLBACK, sanitize
from baza_wiedzy.modules.documents.models import Document, Version
from baza_wiedzy.modules.documents.storage import LocalStorage
from baza_wiedzy.modules.documents.visibility import visibility_clause

log = logging.getLogger(__name__)

EXTENSIONS = {
    "pdf": ".pdf",
    "docx": ".docx",
    "doc": ".doc",
    "txt": ".txt",
    "eml": ".eml",
    "msg": ".msg",
}
CHUNK = 256 * 1024
# Letters NFKD does not decompose into ASCII (Polish ł above all).
_TRANSLITERATE = str.maketrans(
    {"ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ø": "o", "Ø": "O", "ß": "ss", "æ": "ae", "Æ": "AE"}
)
_UNSAFE_ASCII = re.compile(r"[^A-Za-z0-9 ._()+,=@-]")
# RFC 5987 attr-char without the percent sign: everything else is percent-encoded.
_ATTR_SAFE = "!#$&+-.^_`|~"
HEADERS = {
    "x-content-type-options": "nosniff",
    "content-security-policy": "default-src 'none'; sandbox",
    "cache-control": "private, no-store",
}


def download_name(filename: str, kind: str) -> str:
    name = sanitize(filename)
    extension = EXTENSIONS.get(kind, "")
    if extension and not name.lower().endswith(extension):
        name += extension
    return name


def content_disposition(name: str) -> str:
    """`attachment` with an ASCII fallback and the exact name in UTF-8."""
    stem, dot, extension = name.rpartition(".")
    if not dot:
        stem, extension = name, ""
    ascii_stem = unicodedata.normalize("NFKD", stem.translate(_TRANSLITERATE))
    ascii_stem = ascii_stem.encode("ascii", "ignore").decode()
    ascii_stem = " ".join(_UNSAFE_ASCII.sub("_", ascii_stem).split()).strip("._ ") or FALLBACK
    ascii_extension = _UNSAFE_ASCII.sub("_", extension)
    fallback = ascii_stem + (f".{ascii_extension}" if ascii_extension else "")
    encoded = quote(name, safe=_ATTR_SAFE)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


class OriginalMissing(Exception):
    """The stored original is gone (storage damaged or restored incompletely)."""


@dataclass(frozen=True)
class Download:
    path: Path
    size: int
    headers: dict[str, str]


def prepare(
    db: Session,
    user: CurrentUser,
    document_id: uuid.UUID,
    *,
    storage_dir: str | Path,
    ip: str | None,
) -> Download | None:
    """None when the document is not visible (or has no version). Audits the download in the
    caller's transaction; the caller commits before streaming the file."""
    document = db.scalar(
        select(Document).where(Document.id == document_id, visibility_clause(user))
    )
    if document is None:
        return None
    version_id = document.current_version_id()
    version = db.get(Version, version_id) if version_id is not None else None
    if version is None:
        return None
    path = LocalStorage(storage_dir).original_path(version.storage_key)
    if not path.is_file():
        log.error("original file missing: document=%s version=%s", document.id, version.id)
        raise OriginalMissing
    record(
        db,
        "document.download",
        Actor(user.id, user.username),
        entity_type="document",
        entity_id=document.id,
        ip=ip,
        details={"version_id": str(version.id)},
    )
    log.info("document downloaded: document=%s version=%s", document.id, version.id)
    headers = dict(HEADERS)
    headers["content-disposition"] = content_disposition(
        download_name(version.original_filename, version.kind)
    )
    return Download(path=path, size=path.stat().st_size, headers=headers)


def read_chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            yield chunk
