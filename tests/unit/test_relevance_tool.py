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
