"""Helpers of scripts/relevance.py (the local relevance check; it never runs in CI)."""

from __future__ import annotations

from pathlib import Path

import relevance


def test_query_file_with_comments_and_expectations(tmp_path: Path) -> None:
    file = tmp_path / "zapytania.txt"
    file.write_text(
        "# komentarz\n\numowa serwisowa\nNIP 7251801126 => kontrahent\n  lodz  =>  \n",
        encoding="utf-8",
    )
    assert relevance._queries(file) == [
        ("umowa serwisowa", None),
        ("NIP 7251801126", "kontrahent"),
        ("lodz", None),
    ]


def test_snippet_for_the_terminal() -> None:
    assert relevance._plain("Umowa <mark>serwisowa</mark>\n&amp; &lt;b&gt;") == (
        "Umowa «serwisowa» & <b>"
    )


def test_location_labels() -> None:
    origin: list[dict[str, object]] = [
        {"type": "attachment", "name": "a.pdf"},
        {"type": "page", "no": 2},
    ]
    assert relevance._where(origin, None) == "załącznik: a.pdf › strona 2"
    assert relevance._where([], 3) == "strona 3"
    assert relevance._where([], None) == "treść"


def test_waits_for_ocr_but_not_for_documents_on_hold() -> None:
    assert relevance._finished("done", "none")
    assert relevance._finished("failed", "none")
    assert not relevance._finished("pending", "none")
    assert not relevance._finished("needs_ocr", "queued")
    assert not relevance._finished("needs_ocr", "running")
    for settled in ("on_hold", "failed", "skipped", "none"):
        assert relevance._finished("needs_ocr", settled)
    assert relevance._state("needs_ocr", "queued") == "needs_ocr/queued"
    assert relevance._state("done", "done") == "done"
