"""PDF: text per page with pypdfium2, in a child process (sandbox.py, pdf_child.py).

One part per page (origin "strona N"). A page with fewer than OCR_MIN_CHARS non-space characters
that contains an image is a scan. Scans are recognised by OCR (ocr.py, Phase 4, ADR-031) and
stored with `ocr_applied`; a page from which OCR gets fewer than OCR_MIN_CHARS characters is
noted: "no text" at 0 characters, otherwise "very little text (N characters)". When OCR is off,
the PDF has more scanned pages than OCR_MAX_PAGES, or OCR fails (time limit, crash), the scans
stay parts marked `needs_ocr` (with whatever little text they have) and the version ends as
"wymaga OCR"; the text pages are searchable either way, and the document never fails because of
OCR. Works the same for a PDF attached to an e-mail. A page with no text and no image (blank) is
skipped.

Password-protected and damaged files fail permanently with a readable message; more pages than
INGEST_MAX_PAGES is a limit error; running out of the child's memory is permanent (the same
file would fail again); a timeout is transient (retried).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.modules.ingestion import ocr, pdf_child, sandbox
from baza_wiedzy.modules.ingestion.extractors.base import (
    DAMAGED,
    Context,
    LimitExceeded,
    PermanentError,
)

log = logging.getLogger(__name__)

ENCRYPTED = N_("The PDF is password protected. Upload a copy without a password.")
TOO_MANY_PAGES = N_("The PDF has more pages than the allowed limit.")
TOO_BIG = N_("The file needs more memory to process than allowed.")


def read_pages(path: Path, ctx: Context) -> dict[str, object]:
    workdir = ctx.collector.new_dir("pdf")
    target = workdir / "pages.json"
    finished = sandbox.run(
        sandbox.python_child(
            "baza_wiedzy.modules.ingestion.pdf_child",
            str(path),
            str(target),
            str(ctx.limits.max_pages),
        ),
        workdir=workdir,
        timeout=ctx.limits.child_timeout_seconds,
        memory_mb=ctx.limits.child_memory_mb,
    )
    code = finished.returncode
    if code == pdf_child.ENCRYPTED:
        raise PermanentError(ENCRYPTED, "pdf_encrypted")
    if code == pdf_child.TOO_MANY_PAGES:
        raise LimitExceeded(TOO_MANY_PAGES, "page_limit")
    if code == pdf_child.NO_MEMORY:
        raise PermanentError(TOO_BIG, "pdf_memory")
    if finished.killed_by_signal is not None:  # pdfium crashed (or was killed at the limit)
        raise PermanentError(DAMAGED, "pdf_crash")
    if code != pdf_child.OK:
        raise PermanentError(DAMAGED, "pdf_damaged")
    try:
        result: dict[str, object] = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PermanentError(DAMAGED, "pdf_damaged") from exc
    return result


def _letters(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def _recognise(path: Path, pages: list[int], ctx: Context) -> dict[int, str]:
    """OCR text of the scanned pages, or {} (with a note) when OCR does not run or fails."""
    limits = ctx.limits
    if not pages or not limits.ocr_enabled:
        return {}
    if len(pages) > limits.ocr_max_pages:
        ctx.note("ocr_too_many_pages", pages=len(pages), limit=limits.ocr_max_pages)
        return {}
    started = time.monotonic()
    try:
        recognised = ocr.recognise(path, pages, ctx)
    except ocr.OcrFailed as exc:
        log.warning("ocr: failed pages=%d reason=%s", len(pages), exc.reason)
        ctx.note("ocr_failed", reason=exc.reason)
        return {}
    log.info("ocr: pages=%d seconds=%.1f", len(pages), time.monotonic() - started)
    return recognised


def extract(path: Path, ctx: Context) -> None:
    result = read_pages(path, ctx)
    pages = result.get("pages")
    if not isinstance(pages, list):
        raise PermanentError(DAMAGED, "pdf_damaged")
    if ctx.top_level:
        ctx.collector.page_count = len(pages)
    kept: list[tuple[int, str, bool]] = []
    for number, page in enumerate(pages, start=1):
        text = str(page.get("text") or "")
        needs_ocr = _letters(text) < ctx.limits.ocr_min_chars and bool(page.get("has_image"))
        if not text.strip() and not needs_ocr:
            continue
        kept.append((number, text, needs_ocr))
    recognised = _recognise(path, [n for n, _, scan in kept if scan], ctx)
    unreadable: list[int] = []
    little: list[dict[str, int]] = []
    for number, text, needs_ocr in kept:
        if needs_ocr and number in recognised:
            ocr_text = recognised[number]
            found = _letters(ocr_text)
            if found == 0:
                unreadable.append(number)
            elif found < ctx.limits.ocr_min_chars:
                little.append({"page": number, "chars": found})
            ctx.add_text(ocr_text, page_no=number, ocr_applied=True)
        else:
            ctx.add_text(text, page_no=number, needs_ocr=needs_ocr)
    if unreadable:
        ctx.note("ocr_unreadable", pages=unreadable)
    if little:
        ctx.note("ocr_little_text", pages=little)
