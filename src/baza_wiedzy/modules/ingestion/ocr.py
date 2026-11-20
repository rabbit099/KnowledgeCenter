"""OCR of scanned PDF pages: OCRmyPDF + Tesseract in a child process (Phase 4, ADR-031).

Only the pages the PDF extractor marked "needs OCR" are recognised (`--mode force --pages`),
in the languages OCR_LANGUAGES (pol+eng), with automatic page rotation above the confidence
OCR_ROTATE_THRESHOLD (5; OCRmyPDF's default 14 left sideways pages unturned). No `--deskew`:
its fixed-threshold binarisation blanked low-contrast scans, and Tesseract handles a skew of a
few degrees itself (stage A measurements, ADR-031). Only the
text is kept (`--sidecar`, `--output-type none`): no derived PDF is written (decision A1), and
pages are rasterised by pypdfium (`--rasterizer pypdfium`), so Ghostscript is not needed and is
not installed in the worker image.

Isolation as for every native step (sandbox.py, ADR-023): own process group killed at the
time limit (OCR_TIMEOUT_SECONDS for the PDF), address-space limit for each process
(OCR_MEMORY_MB), minimal environment with OMP_THREAD_LIMIT=1 (one thread per Tesseract), at
most OCR_JOBS Tesseract processes, a per-page Tesseract limit (OCR_PAGE_TIMEOUT_SECONDS) and a
page-image limit (OCR_MAX_IMAGE_MPIXELS). The tools' messages go to files in the attempt's
directory and never to the log: they may quote the recognised text.

A failure never fails the document: the caller keeps the pages "needs OCR" with a note.
"""

from __future__ import annotations

import re
from pathlib import Path

from baza_wiedzy.modules.ingestion import sandbox
from baza_wiedzy.modules.ingestion.extractors.base import Context

_SKIPPED = re.compile(r"^\[OCR skipped on page\(s\) (\d+)(?:-(\d+))?\]$")


class OcrFailed(Exception):
    """reason: timeout | crash | failed (no text, no file name)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def command(source: Path, sidecar: Path, pages: list[int], ctx: Context) -> list[str]:
    limits = ctx.limits
    return sandbox.python_child(
        "ocrmypdf",
        "--quiet",
        "--rasterizer", "pypdfium",
        "--output-type", "none",
        "--sidecar", str(sidecar),
        "--mode", "force",
        "--pages", ",".join(str(p) for p in pages),
        "--language", limits.ocr_languages,
        "--rotate-pages",
        "--rotate-pages-threshold", str(limits.ocr_rotate_threshold),
        "--jobs", str(limits.ocr_jobs),
        "--max-image-mpixels", str(limits.ocr_max_image_mpixels),
        "--tesseract-timeout", str(limits.ocr_page_timeout_seconds),
        str(source),
        "-",
    )  # fmt: skip


def parse_sidecar(text: str) -> dict[int, str]:
    """`--sidecar` output -> {page number: text}. Pages are separated by form feeds; runs of
    pages without OCR appear as one "[OCR skipped on page(s) N-M]" segment."""
    result: dict[int, str] = {}
    page = 1
    for segment in text.split("\f"):
        marker = _SKIPPED.match(segment.strip())
        if marker:
            page = int(marker.group(2) or marker.group(1)) + 1
            continue
        result[page] = segment
        page += 1
    return result


def recognise(source: Path, pages: list[int], ctx: Context) -> dict[int, str]:
    """Text of the given pages (1-based); raises OcrFailed."""
    workdir = ctx.collector.new_dir("ocr")
    sidecar = workdir / "text.txt"
    limits = ctx.limits
    try:
        finished = sandbox.run(
            command(source, sidecar, pages, ctx),
            workdir=workdir,
            timeout=limits.ocr_timeout_seconds,
            memory_mb=limits.ocr_memory_mb,
            env={"OMP_THREAD_LIMIT": "1"},
        )
    except sandbox.IsolationTimeout:
        raise OcrFailed("timeout") from None
    if finished.killed_by_signal is not None:
        raise OcrFailed("crash")
    if finished.returncode != 0:
        raise OcrFailed("failed")
    try:
        text = sidecar.read_text(encoding="utf-8")
    except OSError:
        raise OcrFailed("failed") from None
    recognised = parse_sidecar(text)
    return {page: recognised.get(page, "") for page in pages}
