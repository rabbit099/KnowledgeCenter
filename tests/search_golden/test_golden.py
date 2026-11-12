"""Golden search cases from docs/en/search.md ("Golden tests") on synthetic documents."""

from __future__ import annotations

import psycopg
import pytest

from .conftest import search

pytestmark = pytest.mark.integration

Conn = psycopg.Connection[tuple[object, ...]]


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("faktury", {"faktura"}),
        ("fakturze", {"faktura"}),
        ("fakturą", {"faktura"}),
        ("umowie", {"umowa"}),
        ("umów", {"umowa"}),
        ("żółw", {"żółw"}),
        ("pracownikom", {"pracownik"}),
    ],
)
def test_hunspell_base_forms(db: Conn, word: str, expected: set[str]) -> None:
    row = db.execute("SELECT ts_lexize('pl_hunspell', %s)", (word,)).fetchone()
    assert row is not None
    lexemes = row[0]
    assert isinstance(lexemes, list)
    assert expected <= set(lexemes)


def test_hunspell_stop_word(db: Conn) -> None:
    row = db.execute("SELECT ts_lexize('pl_hunspell', 'oraz')").fetchone()
    assert row is not None and row[0] == []


def test_inflected_polish_forms(db: Conn) -> None:
    found = search(db, "faktura")
    assert {"faktury", "fakturze", "faktura_instr"} <= found


def test_inflected_contract(db: Conn) -> None:
    assert {"umowie", "umow"} <= search(db, "umowa")


def test_query_without_diacritics(db: Conn) -> None:
    assert "zolw" in search(db, "zolw")


def test_english_plural(db: Conn) -> None:
    assert "invoices" in search(db, "invoice")


def test_exact_phrase(db: Conn) -> None:
    found = search(db, '"zasady bezpieczeństwa"')
    assert "phrase" in found
    assert "phrase_apart" not in found


def test_exclusion(db: Conn) -> None:
    found = search(db, "faktura -korekta")
    assert "faktury" in found
    assert "faktura_korekta" not in found


def test_exclusion_of_inflected_form(db: Conn) -> None:
    # "korekty" is an inflected form of "korekta": the Polish configuration removes the
    # document, and the other two configurations do not match "faktura" in "faktury".
    assert "korekty_faktury" not in search(db, "faktura -korekta")


def test_literal_server_name(db: Conn) -> None:
    found = search(db, "EX-PVE0")
    assert "server" in found
    assert "server_other" not in found


def test_literal_document_number(db: Conn) -> None:
    found = search(db, "FV/2026/11/0042")
    assert "doc_number" in found
    assert "doc_number_other" not in found


@pytest.mark.skip(reason="needs e-mail extraction (Phase 2)")
def test_word_only_in_email_attachment() -> None: ...


@pytest.mark.skip(reason="needs OCR ingestion (Phase 2)")
def test_word_only_on_scanned_page() -> None: ...
