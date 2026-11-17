"""Word 97-2003 (.doc): LibreOffice headless converts to DOCX, then the DOCX extractor reads it.

Isolation (sandbox.py):
- a separate process group per conversion, killed as a whole after
  LIBREOFFICE_TIMEOUT_SECONDS (transient error: retried, "too long" after the last attempt);
- a fresh LibreOffice profile (-env:UserInstallation) and output directory inside the
  attempt's temporary directory, removed with it;
- an address-space limit (LIBREOFFICE_MEMORY_MB) and the worker container's memory limit;
- one conversion at a time per worker container (a file lock next to the temporary
  directories), so two worker processes never run two LibreOffice instances at once;
- no network (internal Docker network) and a minimal environment.

Password-protected DOC files (FIB flag fEncrypted) are refused before LibreOffice starts.
"""

from __future__ import annotations

import fcntl
import shutil
import time
from pathlib import Path

from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.modules.ingestion import sandbox
from baza_wiedzy.modules.ingestion.extractors import docx
from baza_wiedzy.modules.ingestion.extractors.base import (
    DAMAGED,
    Context,
    PermanentError,
    TransientError,
)

ENCRYPTED = N_("The document is password protected. Upload a copy without a password.")
LOCK_FILE = "libreoffice.lock"
F_ENCRYPTED = 0x0100  # FibBase flags (offset 0x0A): fEncrypted


def is_encrypted(path: Path) -> bool:
    import olefile

    try:
        with olefile.OleFileIO(str(path)) as ole:
            if not ole.exists("WordDocument"):
                return False
            head = ole.openstream("WordDocument").read(12)
    except OSError:
        return False
    return len(head) >= 12 and bool(int.from_bytes(head[10:12], "little") & F_ENCRYPTED)


class _ConversionLock:
    """Exclusive lock shared by all worker processes of the container (same volume)."""

    def __init__(self, path: Path, wait_seconds: float) -> None:
        self.path = path
        self.wait_seconds = wait_seconds

    def __enter__(self) -> _ConversionLock:
        self.handle = self.path.open("a")
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() > deadline:
                    self.handle.close()
                    raise TransientError("libreoffice busy") from None
                time.sleep(0.5)

    def __exit__(self, *_: object) -> None:
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


def convert(path: Path, ctx: Context) -> Path:
    workdir = ctx.collector.new_dir("doc")
    source = workdir / "input.doc"
    shutil.copyfile(path, source)
    out = workdir / "out"
    out.mkdir()
    argv = [
        "soffice",
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nodefault",
        "--nologo",
        f"-env:UserInstallation={(workdir / 'profile').as_uri()}",
        "--convert-to",
        "docx:MS Word 2007 XML",
        "--outdir",
        str(out),
        str(source),
    ]
    limits = ctx.limits
    lock = _ConversionLock(
        ctx.collector.workdir.parent / LOCK_FILE, limits.libreoffice_timeout_seconds
    )
    with lock:
        finished = sandbox.run(
            argv,
            workdir=workdir,
            timeout=limits.libreoffice_timeout_seconds,
            memory_mb=limits.libreoffice_memory_mb,
        )
    result = out / "input.docx"
    if finished.returncode != 0 or not result.is_file() or result.stat().st_size == 0:
        raise PermanentError(DAMAGED, "doc_convert")
    return result


def extract(path: Path, ctx: Context) -> None:
    if is_encrypted(path):
        raise PermanentError(ENCRYPTED, "doc_encrypted")
    docx.extract(convert(path, ctx), ctx, converted=True)
