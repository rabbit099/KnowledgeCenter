"""Virus scan hook: the step before extraction, disabled by default (SCAN_HOOK=none).

ClamAV is not part of Phase 2. When company policy requires it, a scanner is added here
(for example "clamav": send the file to the clamav service of the `antivirus` Compose
profile with INSTREAM) and selected with SCAN_HOOK. A scanner raises PermanentError for an
infected file (the version fails, nothing is extracted) and TransientError when the scanner is
unreachable (the attempt is retried). See docs: ingestion-and-ocr.md, "Virus scan hook".
"""

from __future__ import annotations

from pathlib import Path

from baza_wiedzy.core.config import Settings


def scan(settings: Settings, path: Path) -> None:
    if settings.scan_hook == "none":
        return
    raise ValueError(f"Unknown SCAN_HOOK {settings.scan_hook!r}")  # pragma: no cover
