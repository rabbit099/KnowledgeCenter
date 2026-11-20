"""Identifier normalisation and snippet escaping of the search module (ADR-026, CLAUDE.md 7)."""

from __future__ import annotations

import pytest

from baza_wiedzy.modules.search import identifiers
from baza_wiedzy.modules.search.identifiers import fold, index_tokens, query_alternatives
from baza_wiedzy.modules.search.postgres import negations
from baza_wiedzy.modules.search.snippets import START, STOP, strip_sentinels, to_html


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("NIP 725-18-01-126", {"7251801126", "725", "126"}),
        ("Umowa UM/17/2026 z dnia", {"um172026", "172026", "um17", "um", "2026"}),
        ("tel. +48 600 100 200.", {"48600100200", "600100200", "100200"}),
        ("IT-002, EX-PVE0", {"it002", "expve0", "pve0"}),
        ("ksiegowosc@example.com", {"ksiegowosc", "example.com"}),
        ("jan.kowalski@biuro.example.com", {"jan.kowalski", "jan", "kowalski", "example.com"}),
        ("https://intranet.firma.example.com/x", {"firma.example.com", "example.com"}),
        ("serwer 192.168.10.25", {"192.168.10.25", "192.168.10"}),
        ("Zażółć ŁÓDŹ-2026", {"lodz2026", "lodz"}),
    ],
)
def test_index_tokens(text: str, expected: set[str]) -> None:
    assert expected <= set(index_tokens(text))


@pytest.mark.parametrize(
    "text",
    ["biało-czerwony", "Kwota 1 200,00 zł w 2026 r.", "zwykłe zdanie bez numerów", "999.1.1.1"],
)
def test_no_tokens_for_plain_text(text: str) -> None:
    assert index_tokens(text) == []


def test_token_limit_and_long_chains() -> None:
    assert index_tokens("-".join(["a1"] * 12)) == ["a1" * 12]
    many = " ".join(f"X-{n}" for n in range(10000))
    assert len(index_tokens(many)) == identifiers.MAX_TOKENS


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("725-18-01-126", ("7251801126", "7251801126")),
        ("17/2026", ("172026", "172026")),
        ("UM-17-2026", ("um172026", "um172026")),
        ("+48 600 100 200", ("48600100200", "600100200")),
        ("+48501222333", ("48501222333", "48501222333")),
        ("zażółć 17/2026", ("zażółć 172026", "zażółć 172026")),  # words keep diacritics
        ("faktura -FV/2026/11/0043", ("faktura -fv2026110043", "faktura -fv2026110043")),
        ("Łódź", ("Łódź", "Łódź")),
    ],
)
def test_query_alternatives(query: str, expected: tuple[str, str]) -> None:
    assert query_alternatives(query) == expected


def test_fold() -> None:
    assert fold("Zażółć GĘŚLĄ jaźń, Łódź") == "zazolc gesla jazn, lodz"


def test_negations() -> None:
    assert negations('faktura -korekta -"stara cena" a-b') == 'korekta or "stara cena"'
    assert negations("faktura") == ""


def test_snippet_is_escaped_before_marks_are_added() -> None:
    raw = f'<img src=x onerror="alert(1)"> {START}umowa{STOP} & "cena"'
    html = str(to_html(raw))
    assert html == (
        "&lt;img src=x onerror=&#34;alert(1)&#34;&gt; <mark>umowa</mark> &amp; &#34;cena&#34;"
    )
    assert strip_sentinels(f"a{START}b{STOP}c") == "a b c"
