"""OCR of scanned PDF pages (Phase 4, stage A, ADR-031).

Tests marked `tools` need Tesseract and run in the test image (`make test-db`, CI); the others
run everywhere. All scans are synthetic (tests/fixtures/scans.py).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from baza_wiedzy.modules.ingestion import ocr
from baza_wiedzy.modules.ingestion.extractors.base import Collector, Context, Limits, find
from fixtures.scans import BASE_LINES, SCANS, page_image, scan_pdf

_no_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="Tesseract not installed (runs in make test-db)"
)


def needs_tesseract(test: Any) -> Any:
    return pytest.mark.tools(_no_tesseract(test))


def run(tmp_path: Path, name: str, data: bytes, **limits: Any) -> Collector:
    source = tmp_path / name
    source.write_bytes(data)
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    collector = Collector(limits=Limits(**{"ocr_enabled": True, **limits}), workdir=workdir)
    extractor = find(name.rsplit(".", 1)[1])
    assert extractor is not None
    extractor(source, Context(collector=collector))
    return collector


# --- without tools ------------------------------------------------------------------------


def test_sidecar_is_split_into_pages() -> None:
    sidecar = (
        "[OCR skipped on page(s) 1]\fStrona dwa\n\f[OCR skipped on page(s) 3-5]\fStrona sześć\n"
    )
    assert ocr.parse_sidecar(sidecar) == {2: "Strona dwa\n", 6: "Strona sześć\n"}
    assert ocr.parse_sidecar("Jedyna\n") == {1: "Jedyna\n"}


def test_command_needs_no_ghostscript_and_writes_text_only(tmp_path: Path) -> None:
    ctx = Context(collector=Collector(limits=Limits(ocr_jobs=3), workdir=tmp_path))
    argv = ocr.command(tmp_path / "in.pdf", tmp_path / "out.txt", [2, 4], ctx)
    joined = " ".join(argv)
    assert "--rasterizer pypdfium" in joined and "--output-type none" in joined
    assert "--mode force --pages 2,4" in joined and "--language pol+eng" in joined
    assert "--jobs 3" in joined and argv[-1] == "-"
    assert "ghostscript" not in joined


def test_ocr_off_leaves_scans_marked(tmp_path: Path) -> None:
    collector = run(tmp_path, "skan.pdf", scan_pdf(page_image(BASE_LINES)), ocr_enabled=False)
    assert [p.needs_ocr for p in collector.parts] == [True]
    assert not any(p.ocr_applied for p in collector.parts) and not collector.notes


def test_too_many_scanned_pages_are_left_for_later(tmp_path: Path) -> None:
    collector = run(tmp_path, "skan.pdf", scan_pdf(page_image(BASE_LINES)), ocr_max_pages=0)
    assert [p.needs_ocr for p in collector.parts] == [True]
    assert collector.notes[0]["code"] == "ocr_too_many_pages"
    assert collector.notes[0]["pages"] == 1 and collector.notes[0]["limit"] == 0


# --- with Tesseract --------------------------------------------------------------------------


@needs_tesseract
def test_image_inventory() -> None:
    version = subprocess.run(["tesseract", "--version"], capture_output=True, text=True).stdout
    assert version.startswith("tesseract 5.")
    langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout
    assert {"pol", "eng", "osd"} <= set(langs.split())
    # Not needed (pypdfium rasterises, text only) and AGPL: must not come back (ADR-031).
    assert shutil.which("gs") is None


def _text(collector: Collector) -> str:
    return "\n".join(p.text for p in collector.parts).lower()


@needs_tesseract
@pytest.mark.parametrize("scan", SCANS, ids=[s.filename for s in SCANS])
def test_synthetic_scans_are_recognised(tmp_path: Path, scan: Any) -> None:
    collector = run(tmp_path, scan.filename, scan.build())
    text = _text(collector)
    missing = [word for word in scan.expect if word.lower() not in text]
    assert not missing, f"{scan.filename} ({scan.defect}): not recognised {missing}"
    assert not any(p.needs_ocr for p in collector.parts)
    assert any(p.ocr_applied for p in collector.parts)


@needs_tesseract
def test_mixed_pdf_keeps_the_text_page_and_recognises_the_scan(tmp_path: Path) -> None:
    mixed = next(s for s in SCANS if s.filename == "09-mieszany.pdf")
    collector = run(tmp_path, mixed.filename, mixed.build())
    pages = {p.page_no: p for p in collector.parts}
    assert not pages[1].ocr_applied and "najmu" in pages[1].text
    assert pages[2].ocr_applied and "odbioru" in pages[2].text.lower()


@needs_tesseract
@pytest.mark.parametrize("name", ["10-mail-ze-skanem.eml", "11-outlook-ze-skanem.msg"])
def test_scan_attached_to_an_email(tmp_path: Path, name: str) -> None:
    scan = next(s for s in SCANS if s.filename == name)
    collector = run(tmp_path, name, scan.build())
    recognised = [p for p in collector.parts if p.ocr_applied]
    assert recognised and recognised[0].part_type == "page"
    assert recognised[0].origin[0]["type"] == "attachment"
    assert "odbioru" in recognised[0].text.lower()


@needs_tesseract
def test_time_limit_keeps_the_pages_for_later_and_never_fails(tmp_path: Path) -> None:
    collector = run(tmp_path, "skan.pdf", scan_pdf(page_image(BASE_LINES)), ocr_timeout_seconds=0)
    assert [p.needs_ocr for p in collector.parts] == [True]
    assert collector.notes == [{"code": "ocr_failed", "origin": [], "reason": "timeout"}]


@needs_tesseract
def test_an_empty_scan_is_noted_as_unreadable(tmp_path: Path) -> None:
    from PIL import ImageDraw

    image = page_image([])
    ImageDraw.Draw(image).rectangle((300, 300, 900, 900), outline=0, width=8)  # type: ignore[arg-type]
    collector = run(tmp_path, "pusty.pdf", scan_pdf(image))
    assert not any(p.needs_ocr for p in collector.parts)
    assert collector.notes[-1]["code"] == "ocr_unreadable"
    assert collector.notes[-1]["pages"] == [1]


@needs_tesseract
def test_recognised_text_never_reaches_the_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    collector = run(tmp_path, "skan.pdf", scan_pdf(page_image(BASE_LINES)))
    assert "odbioru" in _text(collector)
    assert "odbioru" not in caplog.text.lower() and "approved" not in caplog.text.lower()
    assert "ocr: pages=1 seconds=" in caplog.text


# --- messages (Polish plural forms 1 / 2-4 / 5+) ---------------------------------------------


def _note(note: dict[str, Any]) -> str:
    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.core.web.templating import make_templates

    env = make_templates(Settings(_env_file=None), ("baza_wiedzy.modules.ingestion",)).env  # type: ignore[call-arg]
    template = '{% from "ingestion/_macros.html" import note_text %}{{ note_text(n) }}'
    return " ".join(env.from_string(template).render(n=note).split())


@pytest.mark.parametrize(
    ("chars", "word"), [(1, "1 znak"), (3, "3 znaki"), (12, "12 znaków"), (22, "22 znaki")]
)
def test_very_little_text_names_the_number_of_characters(chars: int, word: str) -> None:
    text = _note({"code": "ocr_little_text", "origin": [], "pages": [{"page": 2, "chars": chars}]})
    assert f"OCR znalazł bardzo mało tekstu: strona 2 ({word})" in text
    assert "nie znalazł" not in text


def test_no_text_and_too_many_pages_use_plural_forms() -> None:
    one = _note({"code": "ocr_unreadable", "origin": [], "pages": [3]})
    assert "OCR nie znalazł żadnego tekstu na stronie 3" in one
    two = _note({"code": "ocr_unreadable", "origin": [], "pages": [3, 4]})
    assert "na stronach 3, 4" in two
    forms = {
        n: _note({"code": "ocr_too_many_pages", "origin": [], "pages": n, "limit": 0})
        for n in (1, 3, 5)
    }
    assert "1 zeskanowana strona" in forms[1] and "nie da się jej" in forms[1]
    assert "3 zeskanowane strony" in forms[3] and "5 zeskanowanych stron" in forms[5]


@needs_tesseract
def test_a_scan_with_a_few_characters_is_noted_as_very_little_text(tmp_path: Path) -> None:
    collector = run(tmp_path, "malo.pdf", scan_pdf(page_image(["Nr 7"], font_size=60)))
    assert not any(p.needs_ocr for p in collector.parts)
    note = collector.notes[-1]
    assert note["code"] == "ocr_little_text"
    assert note["pages"][0]["page"] == 1 and 0 < note["pages"][0]["chars"] < 25


# --- stage B: decision after the text pass, cancellation ----------------------------------


@pytest.mark.parametrize(
    ("enabled", "pages", "expected"),
    [
        (True, 0, "none"),
        (True, 1, "queued"),
        (True, 30, "queued"),
        (True, 31, "on_hold"),
        (True, 200, "on_hold"),
        (True, 201, "skipped"),
        (False, 5, "none"),
    ],
)
def test_ocr_decision(enabled: bool, pages: int, expected: str) -> None:
    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.modules.ingestion.pipeline import ocr_decision

    settings = Settings(_env_file=None, ocr_enabled=enabled)  # type: ignore[call-arg]
    assert (settings.ocr_auto_max_pages, settings.ocr_max_pages) == (30, 200)
    assert ocr_decision(settings, pages) == expected


def test_a_cancelled_child_is_killed_within_the_poll_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import time

    from baza_wiedzy.modules.ingestion import sandbox

    monkeypatch.setattr(sandbox, "POLL_SECONDS", 0.2)
    calls: list[float] = []

    def stop() -> bool:
        calls.append(time.monotonic())
        return len(calls) >= 2

    started = time.monotonic()
    with pytest.raises(sandbox.IsolationCancelled):
        sandbox.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            workdir=tmp_path,
            timeout=30,
            should_stop=stop,
        )
    assert time.monotonic() - started < 5 and len(calls) == 2
    with pytest.raises(sandbox.IsolationTimeout):
        sandbox.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            workdir=tmp_path,
            timeout=0.5,
            should_stop=lambda: False,
        )


def test_the_document_deadline_is_shared_by_its_pdfs(tmp_path: Path) -> None:
    import time

    collector = Collector(limits=Limits(ocr_enabled=True), workdir=tmp_path)
    collector.ocr_deadline = time.monotonic() - 1  # spent on an earlier PDF of the e-mail
    with pytest.raises(ocr.OcrFailed) as caught:
        ocr.recognise(tmp_path / "x.pdf", [1], Context(collector=collector))
    assert caught.value.reason == "timeout"


# --- stage C: the rotation threshold never turns an upright page ----------------------------


def _rotation_decision(tmp_path: Path, pdf: bytes) -> tuple[bool, float]:
    """Run the production OCR command verbosely and read OCRmyPDF's orientation decision:
    (rotated, confidence; 0 when the page has too little text to decide). Its messages go to
    this test only, never to a log."""
    import os
    import re

    source, sidecar = tmp_path / "in.pdf", tmp_path / "s.txt"
    source.write_bytes(pdf)
    ctx = Context(collector=Collector(limits=Limits(ocr_enabled=True), workdir=tmp_path))
    argv = ocr.command(source, sidecar, [1], ctx)
    argv[argv.index("--quiet")] = "-v1"
    done = subprocess.run(
        argv, capture_output=True, text=True, env={**os.environ, "OMP_THREAD_LIMIT": "1"}
    )
    found = re.search(
        r"facing \S+, confidence ([\d.]+) - (will rotate|rotation appears)", done.stderr
    )
    if found is None:  # too little text for orientation detection: the page is left as it is
        return "will rotate" in done.stderr, 0.0
    return found.group(2) == "will rotate", float(found.group(1))


UPRIGHT = {
    "czysty": lambda: page_image(BASE_LINES),
    "maly-druk": lambda: page_image(BASE_LINES, font_size=22),
    "jeden-wiersz": lambda: page_image(BASE_LINES[:1]),
    "tabela": lambda: page_image(["Zestawienie kosztów"], table=[["Pozycja", "Kwota"]] * 4),
    "przekrzywiony-3": lambda: page_image(BASE_LINES, skew=3),
}


@needs_tesseract
@pytest.mark.parametrize("name", list(UPRIGHT))
def test_an_upright_page_is_never_turned(tmp_path: Path, name: str) -> None:
    from fixtures.scans import scan_pdf as make

    rotated, confidence = _rotation_decision(tmp_path, make(UPRIGHT[name]()))
    assert not rotated, f"{name}: turned at confidence {confidence}"


@needs_tesseract
def test_a_sideways_page_is_turned_upright(tmp_path: Path) -> None:
    rotated, confidence = _rotation_decision(
        tmp_path, scan_pdf(page_image(BASE_LINES, rotate90=True))
    )
    assert rotated and confidence > 5
