"""OCR performance on synthetic scans: `make perf-ocr PAGES=50 [BEST=1]` (Phase 4 stage C).

Runs in the worker test image with the limits of the ocr-worker service (CPUs, memory) and the
production OCR command (ocr.command, sandbox.run, OCR_JOBS, OMP_THREAD_LIMIT=1). One run per
container, so the cgroup's memory.peak is the peak of this run only. Measures:

- one document of PAGES scanned pages: total time and pages per minute;
- the same number of pages as documents of 1-5 pages: time per document, p50 and p95;
- peak anonymous memory of all processes in the container during OCR, and the largest single
  process (Tesseract or OCRmyPDF);
- word recall on pages with defects (the fraction of the known words OCR reads back).

The pages are synthetic (fixed texts, numbers from 600 100 xxx, an invented tax number, every
defect of tests/fixtures/scans.py). Nothing is logged; the results go to stdout only.
`--tessdata DIR` runs with another set of models (tessdata_best for the comparison).
"""

from __future__ import annotations

import argparse
import os
import re
import resource
import shutil
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

from baza_wiedzy.modules.ingestion import ocr, sandbox
from baza_wiedzy.modules.ingestion.extractors.base import Collector, Context, Limits
from fixtures.scans import page_image, scan_pdf

LINES = [
    "Protokół odbioru urządzeń chłodniczych w budynku przy ulicy Zielonej",
    "Komisja stwierdziła usterki instalacji elektrycznej oraz wentylacji",
    "Termin usunięcia usterek ustalono na koniec bieżącego miesiąca",
    "Wykonawca przekaże dokumentację powykonawczą i certyfikaty",
    "The service report was approved by the finance department",
    "All invoices must be archived according to the retention policy",
    "Zażółć gęślą jaźń. Łódź, Kraków, Gdańsk, Wrocław i Poznań",
    "Przegląd techniczny windy osobowej zakończono pozytywnie",
    "Następna kontrola klimatyzatorów zaplanowana jest na wiosnę",
    "Telefon 600 100 201, NIP 123-456-32-18, umowa UM/2026/017",
]
DEFECTS: list[dict[str, object]] = [
    {},
    {"skew": 3.0},
    {"noise": 0.04},
    {"blur": 1.0},
    {"font_size": 24},
    {"ink": 120, "paper": 210},
    {"rotate90": True},
]
SIZES = [1, 2, 3, 1, 5, 2, 4, 1, 3, 2]  # pages per document in the per-document run (cycled)


def words(text: str) -> set[str]:
    return set(re.findall(r"[^\W\d_]{3,}", text.lower()))


TRUTH = words(" ".join(LINES))


def page(n: int) -> object:
    return page_image([f"Strona {n}", *LINES], **DEFECTS[n % len(DEFECTS)])  # type: ignore[arg-type]


def recognise(pdf: bytes, pages: int, args: argparse.Namespace) -> tuple[float, dict[int, str]]:
    workdir = Path(tempfile.mkdtemp(prefix="perf-ocr-"))
    source, sidecar = workdir / "in.pdf", workdir / "text.txt"
    source.write_bytes(pdf)
    limits = Limits(
        ocr_enabled=True, ocr_jobs=args.jobs, ocr_max_pages=10_000, ocr_timeout_seconds=3600
    )
    ctx = Context(collector=Collector(limits=limits, workdir=workdir))
    env = {"OMP_THREAD_LIMIT": "1"}
    if args.tessdata:
        env["TESSDATA_PREFIX"] = args.tessdata
    started = time.monotonic()
    finished = sandbox.run(
        ocr.command(source, sidecar, list(range(1, pages + 1)), ctx),
        workdir=workdir,
        timeout=3600,
        memory_mb=args.memory_mb,
        env=env,
    )
    elapsed = time.monotonic() - started
    if finished.returncode != 0:
        # Synthetic pages only, terminal only: the tool's last lines say why.
        tail = (workdir / "child.err").read_text(errors="replace").splitlines()[-5:]
        raise SystemExit(f"OCR failed with exit code {finished.returncode}:\n" + "\n".join(tail))
    return elapsed, ocr.parse_sidecar(sidecar.read_text(encoding="utf-8"))


class MemorySampler:
    """Largest anonymous memory of the container's processes (cgroup v2 memory.stat "anon")
    while OCR runs, sampled every 0.2 s. Page cache is left out: it is reclaimable and the
    package installation at start would otherwise dominate memory.peak."""

    def __init__(self) -> None:
        self.peak = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def anon() -> int | None:
        try:
            for line in Path("/sys/fs/cgroup/memory.stat").read_text().splitlines():
                key, _, value = line.partition(" ")
                if key == "anon":
                    return int(value)
        except (OSError, ValueError):
            return None
        return None

    def _run(self) -> None:
        while not self._stop.wait(0.2):
            self.peak = max(self.peak, self.anon() or 0)

    def __enter__(self) -> MemorySampler:  # may be entered again; the peak accumulates
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()


def cpu_limit() -> str:
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        return "unlimited" if quota == "max" else f"{int(quota) / int(period):g}"
    except (OSError, ValueError):
        return "unknown"


SYSTEM_TESSDATA = Path("/usr/share/tesseract-ocr/5/tessdata")


def merged_tessdata(models: str) -> str:
    """Tesseract reads its configuration files (configs/, tessconfigs/) from TESSDATA_PREFIX
    too: copy the system directory and put the other models over its own."""
    target = Path(tempfile.mkdtemp(prefix="tessdata-")) / "tessdata"
    shutil.copytree(SYSTEM_TESSDATA, target)
    for model in Path(models).glob("*.traineddata"):
        shutil.copyfile(model, target / model.name)
    return str(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pages", type=int, default=50)
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("OCR_JOBS", "2")))
    parser.add_argument(
        "--memory-mb", type=int, default=int(os.environ.get("OCR_MEMORY_MB", "2048"))
    )
    parser.add_argument("--tessdata", help="directory with pol/eng/osd .traineddata")
    parser.add_argument("--label", default="fast (Debian packages)")
    args = parser.parse_args()
    pages = args.pages
    if args.tessdata:
        args.tessdata = merged_tessdata(args.tessdata)

    print(f"== {args.label}: {pages} pages, OCR_JOBS={args.jobs}, CPU limit {cpu_limit()}")
    images = [page(n) for n in range(pages)]
    sampler = MemorySampler()
    with sampler:
        big, recognised = recognise(scan_pdf(*images), pages, args)
    recall = [len(TRUTH & words(recognised.get(n + 1, ""))) / len(TRUTH) for n in range(pages)]
    per_defect: dict[str, list[float]] = {}
    for n in range(pages):
        name = ",".join(f"{k}={v}" for k, v in DEFECTS[n % len(DEFECTS)].items()) or "clean"
        per_defect.setdefault(name, []).append(recall[n])
    print(f"one document of {pages} pages: {big:.1f} s, {pages / big * 60:.1f} pages/min")

    times: list[float] = []
    done = 0
    i = 0
    with sampler:
        while done < pages:
            size = min(SIZES[i % len(SIZES)], pages - done)
            elapsed, _ = recognise(scan_pdf(*images[done : done + size]), size, args)
            times.append(elapsed)
            done += size
            i += 1
    times.sort()
    p95 = times[min(len(times) - 1, round(0.95 * (len(times) - 1)))]
    print(
        f"{len(times)} documents of 1-5 pages: p50 {statistics.median(times):.1f} s, "
        f"p95 {p95:.1f} s, max {times[-1]:.1f} s, total {sum(times):.1f} s "
        f"({pages / sum(times) * 60:.1f} pages/min)"
    )
    print(f"word recall: {statistics.mean(recall) * 100:.1f} % on average")
    for name, values in per_defect.items():
        print(f"  {name:<22} {statistics.mean(values) * 100:5.1f} %")
    child_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    print(f"largest single process: {child_mb:.0f} MB RSS")
    if sampler.peak:
        print(f"peak memory of all OCR processes (anonymous): {sampler.peak / 1024 / 1024:.0f} MB")
    else:
        print("peak memory of all OCR processes: n/a (no cgroup v2 memory.stat)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
