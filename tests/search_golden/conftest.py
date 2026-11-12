"""Fixtures for the golden search tests (CLAUDE.md section 7, docs/en/search.md).

They need a real PostgreSQL built from docker/db and run through `make test-db`; the search
objects come from the real migrations (`migrated` fixture). Without TEST_DATABASE_URL they are
skipped.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest

from baza_wiedzy.core.config import Settings

CORPUS_DDL = """
CREATE TABLE golden_docs (
    name text PRIMARY KEY,
    body text NOT NULL,
    tsv_pl tsvector GENERATED ALWAYS AS (to_tsvector('pl', body)) STORED,
    tsv_en tsvector GENERATED ALWAYS AS (to_tsvector('english', body)) STORED,
    tsv_simple tsvector GENERATED ALWAYS AS (to_tsvector('simple_unaccent', body)) STORED
);
"""

# Synthetic documents. Each name says what the document is for.
CORPUS: dict[str, str] = {
    "faktury": "Zestawienie: wszystkie faktury za październik zostały opłacone.",
    "fakturze": "W fakturze brakuje numeru NIP odbiorcy.",
    "faktura_instr": "Płatność fakturą przelewową w terminie 14 dni.",
    "faktura_korekta": "Faktura korekta do zamówienia, zmiana ceny jednostkowej.",
    "korekty_faktury": "Korekty faktury wystawiamy tylko po akceptacji działu księgowości.",
    "umowie": "Zgodnie z umową i postanowieniami zawartymi w umowie najmu.",
    "umow": "Rejestr umów serwisowych prowadzi dział administracji.",
    "zolw": "Żółw błotny jest gatunkiem chronionym.",
    "invoices": "All invoices must be approved by the finance department.",
    "phrase": "Obowiązują zasady bezpieczeństwa pracy przy urządzeniach elektrycznych.",
    "phrase_apart": "Ogólne zasady wydawania sprzętu oraz instrukcja bezpieczeństwa magazynu.",
    "server": "Kopia zapasowa serwera EX-PVE0 uruchamia się codziennie o 2:00.",
    "server_other": "Serwer EX-PVE1 jest wyłączony z eksploatacji.",
    "doc_number": "Dotyczy dokumentu FV/2026/11/0042 z dnia 3 listopada.",
    "doc_number_other": "Dotyczy dokumentu FV/2026/11/0043 z dnia 4 listopada.",
}


@pytest.fixture(scope="session")
def db(migrated: Settings) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    url = migrated.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url, autocommit=True) as conn:
        is_super = conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        row = is_super.fetchone()
        assert row is not None and row[0] is False, "tests must run as a non-superuser"
        conn.execute("DROP SCHEMA IF EXISTS golden CASCADE")
        conn.execute("CREATE SCHEMA golden")
        conn.execute("SET search_path = golden, public")
        conn.execute(CORPUS_DDL)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO golden_docs (name, body) VALUES (%s, %s)", list(CORPUS.items())
            )
        yield conn
        conn.execute("DROP SCHEMA golden CASCADE")


def search(conn: psycopg.Connection[tuple[object, ...]], query: str) -> set[str]:
    """Match the way CLAUDE.md section 7 prescribes: three configurations, OR-ed."""
    rows = conn.execute(
        """
        SELECT name FROM golden_docs
        WHERE tsv_pl @@ websearch_to_tsquery('pl', %(q)s)
           OR tsv_en @@ websearch_to_tsquery('english', %(q)s)
           OR tsv_simple @@ websearch_to_tsquery('simple_unaccent', %(q)s)
        """,
        {"q": query},
    ).fetchall()
    return {str(r[0]) for r in rows}
