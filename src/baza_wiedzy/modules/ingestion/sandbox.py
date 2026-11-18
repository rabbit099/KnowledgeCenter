"""External and native-code steps in a separate process (PDF parsing, LibreOffice).

A crash, a hang or runaway memory in native code must not take the worker down:
- the child runs in its own session (process group), so a timeout kills it and everything it
  started (`killpg`, SIGKILL);
- an address-space limit (RLIMIT_AS) caps its memory where the program tolerates it (the PDF
  child does; LibreOffice reserves far more virtual memory than it uses, so it relies on the
  container memory limit instead, see ingestion-and-ocr.md);
- it gets a minimal environment (no proxy variables, its own HOME and TMPDIR inside the
  attempt's temporary directory) and its output goes to files in that directory;
- the worker has no network route to the internet (internal Docker network `backend`, ADR-024).

Timeouts raise IsolationTimeout, a transient error: the attempt is retried and fails with
"processing took longer than the time limit" after the last attempt.
"""

from __future__ import annotations

import contextlib
import logging
import os
import resource
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from baza_wiedzy.modules.ingestion.extractors.base import TransientError

log = logging.getLogger(__name__)


class IsolationTimeout(TransientError):
    pass


@dataclass(frozen=True)
class Finished:
    returncode: int
    killed_by_signal: int | None


def _limits(memory_mb: int | None) -> Callable[[], None]:
    def apply() -> None:
        if memory_mb:
            limit = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply


def _environment(workdir: Path) -> dict[str, str]:
    home = workdir / "home"
    tmp = workdir / "tmp"
    home.mkdir(exist_ok=True)
    tmp.mkdir(exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "LANG": "C.UTF-8",
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
    }


def run(
    argv: Sequence[str],
    *,
    workdir: Path,
    timeout: float,
    memory_mb: int | None = None,
) -> Finished:
    """Run argv in its own process group; raise IsolationTimeout after `timeout` seconds.

    stdout and stderr go to files in workdir (never to the worker log: they may quote
    document content).
    """
    with (workdir / "child.out").open("wb") as out, (workdir / "child.err").open("wb") as err:
        process = subprocess.Popen(
            list(argv),
            cwd=workdir,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            env=_environment(workdir),
            start_new_session=True,
            preexec_fn=_limits(memory_mb),
        )
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(process)
            raise IsolationTimeout("timeout") from None
        finally:
            _kill_group(process)  # leftovers of a finished leader (e.g. LibreOffice helpers)
    signal_number = -returncode if returncode < 0 else None
    return Finished(returncode=returncode, killed_by_signal=signal_number)


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover
        log.warning("isolated process %s did not exit after SIGKILL", process.pid)


def python_child(module: str, *args: str) -> list[str]:
    """argv for `python -m module args...` with the worker's interpreter."""
    return [sys.executable, "-P", "-m", module, *args]  # -P: no working directory on sys.path
