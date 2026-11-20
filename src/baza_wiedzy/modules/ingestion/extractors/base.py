"""Common interface of the extractors.

An extractor is a function `extract(path, ctx)` that reads one file and adds text parts and
notes to `ctx`. Attachments (e-mail) go through `ctx.attachment()`, which checks the type by
content, applies the depth limit and runs the extractor of the detected kind in a child context.

Errors:
    PermanentError  the file itself is the problem (damaged, encrypted, unsupported encoding):
                    the version fails at once with `message` (a gettext id), no retry.
    LimitExceeded   a limit for the whole upload was passed; never downgraded to a note.
    TransientError  the environment is the problem (external process killed, disk busy):
                    the attempt is repeated later.

Messages, notes and log lines never contain document text. Notes may contain the (sanitized)
names of attachments, which the user sees on the document page; they are not logged.
"""

from __future__ import annotations

import logging
import tempfile
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.modules.documents.services import api as documents

log = logging.getLogger(__name__)

Origin = list[dict[str, Any]]


class PermanentError(Exception):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(code)
        self.message = message  # gettext message id for the user
        self.code = code  # short machine-readable reason for the log


class LimitExceeded(PermanentError):
    pass


class TransientError(Exception):
    pass


DAMAGED = N_("The file is damaged or has an unexpected structure and cannot be read.")


@dataclass(frozen=True)
class Limits:
    max_depth: int = 3
    max_parts: int = 2000
    max_text_chars: int = 50 * 1024 * 1024
    max_pages: int = 2000
    ocr_min_chars: int = 25
    child_memory_mb: int = 1024
    child_timeout_seconds: int = 300
    libreoffice_timeout_seconds: int = 120
    libreoffice_memory_mb: int = 1024
    ocr_enabled: bool = False
    ocr_languages: str = "pol+eng"
    ocr_max_pages: int = 200
    ocr_timeout_seconds: int = 600
    ocr_page_timeout_seconds: int = 120
    ocr_jobs: int = 2
    ocr_memory_mb: int = 2048
    ocr_max_image_mpixels: int = 200
    ocr_rotate_threshold: float = 5.0


@dataclass
class RawPart:
    part_type: str  # page | body | attachment
    origin: Origin
    text: str
    page_no: int | None = None
    needs_ocr: bool = False
    ocr_applied: bool = False  # the text comes from OCR (Phase 4)


def format_addresses(pairs: list[tuple[str, str]], limit: int = 4000) -> str | None:
    """One format for sender, To and Cc of EML and MSG: "Name <address>" (only the address when
    there is no name), separated by ", ". Control characters removed, length limited."""
    items: list[str] = []
    for name, address in pairs:
        name = clean_text(" ".join((name or "").split())).strip('"')
        address = clean_text((address or "").strip())
        if not address and not name:
            continue
        if name and address and name.lower() != address.lower():
            items.append(f"{name} <{address}>")
        else:
            items.append(address or name)
    text = ", ".join(items)[:limit]
    return text or None


@dataclass
class EmailHeaders:
    subject: str | None = None
    from_addr: str | None = None
    to_addrs: str | None = None
    cc_addrs: str | None = None
    sent_at: datetime | None = None
    message_id: str | None = None


@dataclass
class Collector:
    """Everything one version produces; shared by the context of the file and its attachments."""

    limits: Limits
    workdir: Path
    parts: list[RawPart] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    chars: int = 0
    page_count: int | None = None
    email: EmailHeaders | None = None
    files: int = 0

    def temp_file(self) -> Path:
        self.files += 1
        return self.workdir / f"attachment-{self.files}"

    def new_dir(self, prefix: str) -> Path:
        """A fresh directory inside the attempt's temporary directory (child processes)."""
        return Path(tempfile.mkdtemp(prefix=f"{prefix}-", dir=self.workdir))


Extractor = Callable[[Path, "Context"], None]


def clean_text(text: str) -> str:
    """Drop characters PostgreSQL text cannot hold and control characters; unify line ends."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cs", "Co")
    ).strip()


@dataclass
class Context:
    collector: Collector
    origin: Origin = field(default_factory=list)
    depth: int = 0

    @property
    def limits(self) -> Limits:
        return self.collector.limits

    @property
    def top_level(self) -> bool:
        return self.depth == 0

    def add_text(
        self,
        text: str,
        *,
        page_no: int | None = None,
        needs_ocr: bool = False,
        ocr_applied: bool = False,
    ) -> None:
        text = clean_text(text)
        if not text and not needs_ocr:
            return
        collector = self.collector
        collector.chars += len(text)
        if collector.chars > self.limits.max_text_chars:
            raise LimitExceeded(
                N_("The file contains more text than the allowed limit."), "text_limit"
            )
        if len(collector.parts) >= self.limits.max_parts:
            raise LimitExceeded(N_("The file has more parts than the allowed limit."), "part_limit")
        part_type = "page" if page_no is not None else ("body" if self.top_level else "attachment")
        collector.parts.append(
            RawPart(
                part_type=part_type,
                origin=list(self.origin),
                text=text,
                page_no=page_no,
                needs_ocr=needs_ocr,
                ocr_applied=ocr_applied,
            )
        )

    def note(self, code: str, **values: Any) -> None:
        self.collector.notes.append({"code": code, "origin": list(self.origin), **values})

    def nested(self, raw_name: str) -> Context | None:
        """Context of an attachment (origin + depth); None, with a note, when too deep."""
        name = documents.sanitize_filename(raw_name)
        child = Context(
            collector=self.collector,
            origin=[*self.origin, {"type": "attachment", "name": name}],
            depth=self.depth + 1,
        )
        if child.depth > self.limits.max_depth:
            child.note("skipped", reason="too_deep")
            return None
        return child

    @property
    def name(self) -> str:
        """Sanitized name of the attachment this context belongs to ("" at the top)."""
        return str(self.origin[-1].get("name", "")) if self.origin else ""

    def attachment(self, raw_name: str, data: bytes) -> None:
        """Process an attachment: own origin segment, depth + 1, type checked by content."""
        child = self.nested(raw_name)
        if child is None:
            return
        name = child.name
        path = self.collector.temp_file()
        try:
            path.write_bytes(data)
            try:
                kind = documents.detect_kind(path, name)
            except documents.UnsupportedFile as exc:
                encrypted = "password" in exc.message
                child.note("skipped", reason="encrypted" if encrypted else "unsupported")
                return
            extractor = find(kind)
            if extractor is None:
                child.note("skipped", reason="not_yet", kind=kind)
                return
            try:
                extractor(path, child)
            except (LimitExceeded, TransientError):
                raise
            except PermanentError as exc:
                log.info("attachment not readable: reason=%s kind=%s", exc.code, kind)
                encrypted = exc.code.endswith("_encrypted")
                child.note(
                    "skipped" if encrypted else "failed",
                    reason="encrypted" if encrypted else exc.code,
                )
            except Exception as exc:  # a parser crash on one attachment spares the others
                log.warning(
                    "attachment extractor crashed: kind=%s error=%s", kind, type(exc).__name__
                )
                child.note("failed", reason="error")
        finally:
            path.unlink(missing_ok=True)


def find(kind: str) -> Extractor | None:
    """The extractor for a kind, imported on first use (heavy libraries load lazily)."""
    from baza_wiedzy.modules.ingestion.extractors import REGISTRY

    ref = REGISTRY.get(kind)
    if ref is None:
        return None
    from baza_wiedzy.core.registry import load_ref

    extractor: Extractor = load_ref(ref)
    return extractor
