"""PDF: text per page with pypdfium2, in a child process (sandbox.py, pdf_child.py).

One part per page (origin "strona N"). A page with fewer than OCR_MIN_CHARS non-space
characters that contains an image is a scan: it is kept as a part marked `needs_ocr` (with
whatever little text it has), and the version ends as "wymaga OCR" instead of "gotowe" — the
text pages are searchable already, Phase 4 adds OCR for the marked pages. A page with no text
and no image (blank) is skipped.

Password-protected and damaged files fail permanently with a readable message; more pages than
INGEST_MAX_PAGES is a limit error; running out of the child's memory is permanent (the same
file would fail again); a timeout is transient (retried).
"""

from __future__ import annotations

import json
from pathlib import Path

from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.modules.ingestion import pdf_child, sandbox
from baza_wiedzy.modules.ingestion.extractors.base import (
    DAMAGED,
    Context,
    LimitExceeded,
    PermanentError,
)

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


def extract(path: Path, ctx: Context) -> None:
    result = read_pages(path, ctx)
    pages = result.get("pages")
    if not isinstance(pages, list):
        raise PermanentError(DAMAGED, "pdf_damaged")
    if ctx.top_level:
        ctx.collector.page_count = len(pages)
    for number, page in enumerate(pages, start=1):
        text = str(page.get("text") or "")
        letters = sum(1 for ch in text if not ch.isspace())
        needs_ocr = letters < ctx.limits.ocr_min_chars and bool(page.get("has_image"))
        if not text.strip() and not needs_ocr:
            continue
        ctx.add_text(text, page_no=number, needs_ocr=needs_ocr)
