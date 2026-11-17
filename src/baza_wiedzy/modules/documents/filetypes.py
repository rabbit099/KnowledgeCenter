"""File type detection by content (signatures and structure), never by name or Content-Type.

ADR-021: own checks instead of libmagic, which cannot tell DOC from MSG (both OLE2 compound
files) and is not in the app image. The extension must be in the allow-list and must match the
detected type.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import olefile

from baza_wiedzy.core.web.i18n import N_

ALLOWED = ("pdf", "docx", "doc", "txt", "eml", "msg")
OLE2_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
ZIP_MAGIC = b"PK\x03\x04"
HEAD_BYTES = 64 * 1024
# A header field of RFC 5322: name, colon (no whitespace before it).
_HEADER_LINE = re.compile(rb"^[!-9;-~]+:")
_MAIL_FIELDS = (b"from", b"to", b"subject", b"date", b"message-id", b"received", b"mime-version")


class UnsupportedFile(ValueError):
    """The file is not accepted. `message` is an English gettext id shown to the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _is_pdf(head: bytes) -> bool:
    # The PDF header may be preceded by up to 1024 bytes of junk (PDF 1.7, 7.5.2 note).
    return b"%PDF-" in head[:1024]


def _is_docx(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:  # reads only the central directory
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
    return "[Content_Types].xml" in names and "word/document.xml" in names


def _ole_kind(path: Path) -> str | None:
    try:
        ole = olefile.OleFileIO(str(path))
    except (OSError, ValueError, IndexError, TypeError) as exc:  # olefile raises generic errors
        del exc
        return None
    try:
        if ole.exists("WordDocument"):
            return "doc"
        if ole.exists("__properties_version1.0"):
            return "msg"
        return None  # another OLE2 file (old .xls, .ppt, ...): not accepted
    finally:
        ole.close()


def _looks_like_mail(head: bytes) -> bool:
    if b"\0" in head:
        return False
    block = head.replace(b"\r\n", b"\n").split(b"\n\n", 1)[0]
    lines = [line for line in block.split(b"\n") if line and not line[:1].isspace()]
    if not lines or not all(_HEADER_LINE.match(line) for line in lines):
        return False
    names = {line.split(b":", 1)[0].strip().lower() for line in lines}
    return b"from" in names and len(names & set(_MAIL_FIELDS)) >= 2


def _looks_like_text(head: bytes) -> bool:
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True  # UTF-16 with BOM: the extractor decodes it
    return b"\0" not in head


def detect(path: Path, filename: str) -> str:
    """Return the kind (one of ALLOWED) or raise UnsupportedFile with a readable message."""
    extension = extension_of(filename)
    if extension not in ALLOWED:
        raise UnsupportedFile(
            N_("This file type is not supported. Allowed: PDF, DOCX, DOC, TXT, EML, MSG.")
        )
    with path.open("rb") as handle:
        head = handle.read(HEAD_BYTES)
    if not head:
        raise UnsupportedFile(N_("The file is empty."))
    if _is_pdf(head):
        kind: str | None = "pdf"
    elif head.startswith(ZIP_MAGIC):
        kind = "docx" if _is_docx(path) else None
    elif head.startswith(OLE2_MAGIC):
        kind = _ole_kind(path)
    elif extension == "eml":
        kind = "eml" if _looks_like_mail(head) else None
    elif extension == "txt":
        kind = "txt" if _looks_like_text(head) else None
    else:
        kind = None
    if kind != extension:
        raise UnsupportedFile(
            N_("The content of the file does not match its extension, or the file is damaged.")
        )
    return kind
