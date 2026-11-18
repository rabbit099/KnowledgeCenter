"""Search performance measurement on a synthetic corpus: `make perf N=20000` (manual, not CI).

Runs in the test image against a throwaway PostgreSQL with the production settings
(docker-compose.perf.yml: max_connections 40, shared_buffers 256 MB, 2 GB memory limit, data on
a volume). Steps, each timed and written to perf-results/perf-<N>.md and .json:

1. migrate; generate N documents (deterministic; Polish and English text with a Zipf
   vocabulary, real business words at the top, identifiers, e-mails, drafts) straight into the
   tables of documents and ingestion (COPY);
2. full index with the real indexer (search.indexer.index_many): time and documents per minute
   of one process (= one worker process handling document.version.extracted);
3. VACUUM ANALYZE, then sizes: heap, TOAST, each GIN index, the trigram index, and the
   tsvector columns themselves;
4. queries (selective, typical, broad, phrase, exclusion, identifiers, typo, filters, sort by
   date) as a Reader and as Admin: p50/p95/max of the whole backend call (ranking, count,
   headlines, details), warm cache; EXPLAIN (ANALYZE, BUFFERS) of the main statement for three
   of them;
5. 10 concurrent searches (threads, own connections): p50/p95 and searches per second;
6. evaluation of a Hunspell dictionary without diacritics (pl_unaccent, files made by the
   `dict` service of docker-compose.perf.yml): memory per connection, extra space, collisions
   of real words (laska/łaska), behaviour of "lodz"/"lodzi".

Only synthetic text; nothing here reads real documents. Prints results, never document text.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import random
import statistics
import sys
import threading
import time
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy import event, text

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.migrate import upgrade
from baza_wiedzy.core.db.session import make_engine, make_session_factory
from baza_wiedzy.core.permissions.service import CurrentUser
from baza_wiedzy.modules.search import indexer
from baza_wiedzy.modules.search.backend import SearchRequest
from baza_wiedzy.modules.search.postgres import PostgresBackend

# --- synthetic text --------------------------------------------------------------------------

# Real words at the top of the Zipf ranking (inflected forms included), so that ranking, the
# Polish dictionary and broad queries behave like on real documents.
PL_COMMON = """
i w na z do się że jest nie to o dla przez od po przy oraz lub jak także który która umowa
umowy umowie umową umów faktura faktury fakturze fakturą faktur zamówienie zamówienia
dostawa dostawy dostawę usługa usługi usług serwis serwisu przegląd przeglądu przeglądy
urządzenie urządzenia urządzeń pracownik pracownika pracowników klient klienta klientów
termin terminu płatność płatności kwota kwoty netto brutto podatek vat zł budynek budynku
lokal lokalu spółdzielnia spółdzielni zarząd zarządu protokół protokołu odbiór odbioru
zgłoszenie zgłoszenia awaria awarii naprawa naprawy instrukcja instrukcji procedura
procedury bezpieczeństwo bezpieczeństwa zasady zasad praca pracy projekt projektu raport
raportu miesiąc miesiąca rok roku dzień dni styczeń luty marzec kwiecień maj czerwiec lipiec
sierpień wrzesień październik listopad grudzień wykonawca wykonawcy zamawiający
zamawiającego strona strony aneks aneksu załącznik załącznika kopia kopii dokument dokumentu
dokumentów system systemu serwer serwera sieć sieci dostęp dostępu hasło hasła konto konta
użytkownik użytkownika kotłownia kotłowni winda windy klimatyzacja klimatyzacji ogrzewanie
ogrzewania woda wody energia energii licznik licznika odczyt odczytu rozliczenie rozliczenia
koszt koszty kosztów budżet budżetu
""".split()  # noqa: SIM905  (a word list)
EN_COMMON = """
the of and to in a is that for on with as be by this are from or at contract invoice
invoices delivery service services report reports payment payments customer customers server
servers network access password account user users backup backups policy maintenance
inspection building equipment safety procedure procedures request requests approval approved
finance department team project projects month year date due amount total supplier suppliers
order orders quote quotes meeting minutes agreement terms
""".split()  # noqa: SIM905  (a word list)
SYLLABLES = """
ka ko ku ki ma mo mi na no ni ra ro ry ta to ty wa wo za zo ło ża śc cz sz rz pr st kr gr dr
br pl tr sk sp dz an en on ar er or ol el ik ek uk ow ew ia ie io
""".split()  # noqa: SIM905  (a word list)


def pseudo_words(rng: random.Random, count: int) -> list[str]:
    seen: set[str] = set()
    words: list[str] = []
    while len(words) < count:
        word = "".join(rng.choice(SYLLABLES) for _ in range(rng.randint(2, 4)))
        if word not in seen:
            seen.add(word)
            words.append(word)
    return words


class Vocabulary:
    """Zipf(s=1.07) over real words first, then pseudo-words (the long tail)."""

    def __init__(self, rng: random.Random, common: list[str], tail: int) -> None:
        self.words = common + pseudo_words(rng, tail)
        weights = [1.0 / (rank + 1) ** 1.07 for rank in range(len(self.words))]
        total = sum(weights)
        self.cum: list[float] = []
        acc = 0.0
        for w in weights:
            acc += w / total
            self.cum.append(acc)
        self.rng = rng

    def sample(self, k: int) -> list[str]:
        import bisect

        return [
            self.words[min(bisect.bisect(self.cum, self.rng.random()), len(self.words) - 1)]
            for _ in range(k)
        ]


def sentence(vocab: Vocabulary, rng: random.Random) -> str:
    words = vocab.sample(rng.randint(6, 18))
    words[0] = words[0].capitalize()
    return " ".join(words) + "."


@dataclass
class Corpus:
    users: dict[str, uuid.UUID] = field(default_factory=dict)
    documents: int = 0
    parts: int = 0
    chars: int = 0
    drafts: int = 0
    emails: int = 0
    samples: dict[str, str] = field(default_factory=dict)  # existing values for the queries


def nip(rng: random.Random) -> str:
    d = f"{rng.randint(100, 999)}{rng.randint(10, 99)}{rng.randint(10, 99)}{rng.randint(100, 999)}"
    return f"{d[:3]}-{d[3:5]}-{d[5:7]}-{d[7:]}" if rng.random() < 0.5 else d


def generate(conn: psycopg.Connection[Any], n: int, seed: int = 20261118) -> Corpus:
    rng = random.Random(seed)
    pl = Vocabulary(rng, PL_COMMON, 60000)
    en = Vocabulary(rng, EN_COMMON, 20000)
    corpus = Corpus()
    with conn.cursor() as cur:
        for i in range(6):
            uid = uuid.uuid4()
            cur.execute(
                "INSERT INTO users (id, username, display_name, source) VALUES (%s,%s,%s,'ad')",
                (uid, f"perf-owner-{i}", f"Autor Testowy {i}"),
            )
            corpus.users[f"owner{i}"] = uid
    owners = list(corpus.users.values())
    start = datetime(2019, 1, 1, tzinfo=UTC)
    docs_buf, vers_buf, parts_buf, res_buf, mail_buf = (io.StringIO() for _ in range(5))

    def row(buf: io.StringIO, *values: object) -> None:
        def fmt(v: object) -> str:
            if v is None:
                return "\\N"
            s = str(v)
            return s.replace("\\", "\\\\").replace("\t", " ").replace("\n", "\\n")

        buf.write("\t".join(fmt(v) for v in values) + "\n")

    for i in range(n):
        doc_id, ver_id = uuid.uuid4(), uuid.uuid4()
        english = rng.random() < 0.2
        vocab = en if english else pl
        is_mail = rng.random() < 0.15
        kind = "eml" if is_mail else rng.choice(("pdf", "pdf", "docx", "txt", "doc"))
        n_parts = 1 + min(int(rng.expovariate(1 / 2.2)), 30) if kind == "pdf" else rng.randint(1, 3)
        status = "draft" if rng.random() < 0.1 else "published"
        corpus.drafts += status == "draft"
        owner = rng.choice(owners)
        when = start + timedelta(minutes=rng.randint(0, 8 * 365 * 24 * 60))
        number = f"FV/{when.year}/{when.month:02d}/{i:05d}"
        title_words = " ".join(vocab.sample(rng.randint(2, 5))).capitalize()
        title = f"{title_words} {number}" if rng.random() < 0.3 else title_words
        row(docs_buf, doc_id, title, "user", owner, "draft", status, None, None, when, when)
        row(
            vers_buf,
            ver_id,
            doc_id,
            1,
            kind,
            f"{title_words[:40]}-{i}.{kind}",
            1000 + i,
            f"{i:064x}",
            uuid.uuid4().hex,
            owner,
            when,
            "done",
            1,
            when,
            1 if kind != "pdf" else n_parts,
        )
        doc_chars = 0
        for seq in range(1, n_parts + 1):
            body = " ".join(sentence(vocab, rng) for _ in range(rng.randint(4, 14)))
            if rng.random() < 0.15:
                tax = nip(rng)
                body += f" Faktura nr {number}, NIP {tax}."
                corpus.samples.setdefault("number", number)
                corpus.samples.setdefault("nip", tax.replace("-", ""))
            if rng.random() < 0.05:
                phone = f"{rng.randint(500, 899)} {rng.randint(100, 999)} {rng.randint(100, 999)}"
                body += f" Kontakt: tel. +48 {phone}."
                corpus.samples.setdefault("phone", phone)
            if rng.random() < 0.03:
                host = f"10.20.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
                body += f" Serwer EX-PVE{rng.randint(0, 40)} pod adresem {host}."
            page = seq if kind == "pdf" else None
            origin = json.dumps([{"type": "page", "no": seq}] if page else [])
            row(
                parts_buf,
                ver_id,
                seq,
                "page" if page else "body",
                origin,
                page,
                1,
                "en" if english else "pl",
                body,
                len(body),
            )
            corpus.parts += 1
            corpus.chars += len(body)
            doc_chars += len(body)
        row(res_buf, ver_id, n_parts, doc_chars)
        if is_mail:
            corpus.emails += 1
            row(
                mail_buf,
                ver_id,
                title,
                f"Nadawca {i % 300} <nadawca{i % 300}@example.com>",
                f"biuro@example.com, osoba{i % 97}@example.com",
                None,
                when,
                f"<m{i}@example.com>",
            )
    corpus.documents = n
    with conn.cursor() as cur:
        copies = (
            (
                "doc_documents (id, title, title_source, owner_id, status, description, "
                "published_version_id, "
                "latest_version_id, created_at, updated_at)",
                docs_buf,
            ),
            (
                "doc_versions (id, document_id, version_no, kind, original_filename, size_bytes, "
                "sha256, storage_key, uploaded_by, uploaded_at, processing_status, "
                "processing_attempts, processed_at, page_count)",
                vers_buf,
            ),
            (
                "ing_parts (version_id, seq, part_type, origin, page_no, chunk_no, language, text, "
                "char_count)",
                parts_buf,
            ),
            ("ing_results (version_id, parts_count, chars_total)", res_buf),
            (
                "doc_email_metadata (version_id, subject, from_addr, to_addrs, cc_addrs, sent_at, "
                "message_id)",
                mail_buf,
            ),
        )
        for target, buf in copies:
            with cur.copy(f"COPY {target} FROM STDIN") as copy:
                copy.write(buf.getvalue())
        cur.execute(
            # Rows arrive as drafts (a published document must name its version); the target
            # status travels in `description` and is applied here.
            "UPDATE doc_documents d SET latest_version_id = v.id, status = d.description, "
            "published_version_id = CASE WHEN d.description = 'published' THEN v.id END, "
            "published_at = CASE WHEN d.description = 'published' THEN d.created_at END, "
            "description = NULL FROM doc_versions v WHERE v.document_id = d.id"
        )
    conn.commit()
    return corpus


# --- measurement helpers ----------------------------------------------------------------------


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(p / 100 * len(ordered)) - 1)]


def summary(values: list[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "p50_ms": round(statistics.median(values) * 1000, 1),
        "p95_ms": round(pct(values, 95) * 1000, 1),
        "max_ms": round(max(values) * 1000, 1),
    }


def mb(value: int | None) -> float:
    return round((value or 0) / 1024 / 1024, 1)


READER = CurrentUser(
    uuid.uuid4(), "perf-reader", "Czytelnik", "ad", False, frozenset({"documents.read"})
)
ADMIN_CODES = frozenset(
    {
        "documents.read",
        "documents.read_drafts",
        "documents.edit_any",
        "documents.publish_any",
        "documents.publish",
    }
)
ADMIN = CurrentUser(uuid.uuid4(), "perf-admin", "Admin", "local", True, ADMIN_CODES)


def queries(samples: dict[str, str]) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("selective: invoice number", {"query": samples.get("number", "FV/2023/07/00042")}),
        ("selective: rare word", {"query": "kotłowni awaria"}),
        ("typical: two words", {"query": "przegląd urządzeń"}),
        ("typical: inflected", {"query": "umowie dostawy"}),
        ("typical: English", {"query": "backup policy"}),
        ("phrase", {"query": '"protokół odbioru"'}),
        ("exclusion", {"query": "faktura -korekta"}),
        ("identifier: NIP digits", {"query": samples.get("nip", "7251801126")}),
        ("identifier: phone", {"query": samples.get("phone", "603 388 100")}),
        ("typo in title (trigram)", {"query": "harmonogran przeglądów"}),
        (
            "filter: kind + date",
            {"query": "faktura", "kinds": ("pdf",), "since": datetime(2024, 1, 1, tzinfo=UTC)},
        ),
        ("sort by date", {"query": "umowa", "sort": "date"}),
        ("broad: one common word", {"query": "umowa"}),
        ("broad: very common word", {"query": "przez"}),
    ]


def run_queries(
    factory: Any, backend: PostgresBackend, reps: int, cases: list[tuple[str, dict[str, Any]]]
) -> tuple[dict[str, Any], list[float]]:
    out: dict[str, Any] = {}
    every: list[float] = []
    for label, params in cases:
        for who, user in (("reader", READER), ("admin", ADMIN)):
            times: list[float] = []
            total = fallback = 0
            for rep in range(reps + 1):
                with factory() as db:
                    started = time.perf_counter()
                    page = backend.search(db, user, SearchRequest(**params))
                    elapsed = time.perf_counter() - started
                if rep:  # the first run warms the cache
                    times.append(elapsed)
                total, fallback = page.total, fallback + page.by_date_fallback
            out[f"{label} [{who}]"] = {**summary(times), "found": total, "fallbacks": fallback}
            every += times
    return out, every


def explain(engine: Any, factory: Any, backend: PostgresBackend, query: str) -> str:
    captured: list[tuple[str, Any]] = []

    def grab(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if "count(*) OVER ()" in statement:
            captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", grab)
    try:
        with factory() as db:
            backend.search(db, READER, SearchRequest(query=query))
    finally:
        event.remove(engine, "before_cursor_execute", grab)
    statement, parameters = captured[-1]
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("SET statement_timeout = 0")
        cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + statement, parameters)
        return "\n".join(r[0] for r in cur.fetchall())
    finally:
        raw.close()


def concurrent(
    factory: Any,
    backend: PostgresBackend,
    cases: list[tuple[str, dict[str, Any]]],
    threads: int,
    per_thread: int,
) -> dict[str, Any]:
    times: list[float] = []
    lock = threading.Lock()
    mix = [p for _, p in cases]

    def worker(k: int) -> None:
        rng = random.Random(k)
        for _ in range(per_thread):
            params = rng.choice(mix)
            with factory() as db:
                started = time.perf_counter()
                backend.search(db, rng.choice((READER, ADMIN)), SearchRequest(**params))
                elapsed = time.perf_counter() - started
            with lock:
                times.append(elapsed)

    started = time.perf_counter()
    pool = [threading.Thread(target=worker, args=(k,)) for k in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    wall = time.perf_counter() - started
    return {
        **summary(times),
        "threads": threads,
        "searches_per_second": round(len(times) / wall, 1),
    }


def sizes(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    q = conn.execute
    result: dict[str, Any] = {}
    for table in ("srch_parts", "srch_documents", "ing_parts"):
        heap, toast, idx, total = q(
            "SELECT pg_relation_size(c.oid), COALESCE(pg_total_relation_size(c.reltoastrelid), 0),"
            " pg_indexes_size(c.oid), pg_total_relation_size(c.oid) FROM pg_class c"
            " WHERE relname = %s",
            (table,),
        ).fetchone() or (0, 0, 0, 0)
        result[table] = {
            "heap_mb": mb(heap),
            "toast_mb": mb(toast),
            "indexes_mb": mb(idx),
            "total_mb": mb(total),
        }
    result["indexes"] = {
        name: mb(size)
        for name, size in q(
            "SELECT indexrelname, pg_relation_size(indexrelid) FROM pg_stat_user_indexes "
            "WHERE relname IN ('srch_parts', 'srch_documents') ORDER BY 2 DESC"
        ).fetchall()
    }
    result["tsvector_columns"] = {
        col: mb(v)
        for col, v in zip(
            ("tsv_pl", "tsv_en", "tsv_simple", "text_content", "ident_terms"),
            q(
                "SELECT sum(pg_column_size(tsv_pl)), sum(pg_column_size(tsv_en)), "
                "sum(pg_column_size(tsv_simple)), sum(pg_column_size(text_content)), "
                "sum(pg_column_size(ident_terms)) FROM srch_parts"
            ).fetchone()
            or (),
            strict=False,
        )
    }
    result["database_mb"] = mb(q("SELECT pg_database_size(current_database())").fetchone()[0])  # type: ignore[index]
    return result


# --- dictionary without diacritics --------------------------------------------------------------

# (text, query typed without diacritics). Confusions: a different word whose form, without
# diacritics, inflects like the query. Gains: the same word in another form.
CONFUSIONS = [
    ("łaski", "laska"),
    ("łaską", "laski"),
    ("sądu", "sad"),
    ("sądem", "sadu"),
    ("żony", "zona"),
    ("kąta", "kat"),
    ("żalu", "zal"),
    ("łaty", "lata"),
    ("stanie", "stan"),
    ("rządu", "rzad"),
    ("bąka", "bak"),
    ("więcej", "wiec"),
    ("pąsu", "pas"),
    ("ćmy", "cma"),
]
GAINS = [
    ("w Łodzi", "lodz"),
    ("w Gdańsku", "gdansk"),
    ("ze źródła", "zrodlo"),
    ("świadczeń", "swiadczenie"),
    ("zaświadczenia", "zaswiadczenie"),
    ("pracownikach", "pracownik"),
    ("urządzeń", "urzadzenie"),
    ("żółwia", "zolw"),
    ("gęśli", "gesl"),
    ("ułożenia", "ulozenie"),
]
PAIRS = CONFUSIONS


def fold(word: str) -> str:
    word = word.replace("ł", "l").replace("Ł", "L")
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))


def dictionary_memory(dsn: str, dictionary: str) -> int:
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT ts_lexize(%s::regdictionary, 'faktury')", (dictionary,))
        row = conn.execute(
            "SELECT COALESCE(sum(total_bytes), 0) FROM pg_backend_memory_contexts "
            "WHERE name = 'TS dictionary'"
        ).fetchone()
        return int(row[0]) if row else 0


def unaccented(dsn: str, dict_dir: Path, conn: psycopg.Connection[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    conn.execute(
        "CREATE TEXT SEARCH DICTIONARY pl_unaccent_hunspell (TEMPLATE = ispell, "
        "DictFile = pl_unaccent, AffFile = pl_unaccent, StopWords = polish)"
    )
    conn.execute("CREATE TEXT SEARCH CONFIGURATION pl_unaccent (COPY = public.simple_unaccent)")
    conn.execute(
        "ALTER TEXT SEARCH CONFIGURATION pl_unaccent ALTER MAPPING FOR asciiword, word, numword, "
        "asciihword, hword, numhword, hword_asciipart, hword_part, hword_numpart "
        "WITH public.unaccent, pl_unaccent_hunspell, pg_catalog.simple"
    )
    conn.commit()
    # pg_backend_memory_contexts is readable by superusers only (PostgreSQL 16): the throwaway
    # perf database's superuser measures; the application never needs this right.
    superuser = os.environ.get("PERF_SUPERUSER_URL", dsn)
    per_pl = dictionary_memory(superuser, "public.pl_hunspell")
    per_un = dictionary_memory(superuser, "public.pl_unaccent_hunspell")
    out["memory_per_connection_mb"] = {"pl_hunspell": mb(per_pl), "pl_unaccent": mb(per_un)}
    out["memory_at_40_connections_mb"] = {
        "pl_hunspell": mb(per_pl * 40),
        "both": mb((per_pl + per_un) * 40),
    }
    lex = {}
    for word in (
        "lodzi",
        "łodzi",
        "lodz",
        "laska",
        "łaska",
        "sad",
        "sąd",
        "zona",
        "faktury",
        "zolwia",
    ):
        row = conn.execute(
            "SELECT ts_lexize('pl_unaccent_hunspell', unaccent(%s))", (word,)
        ).fetchone()
        lex[word] = row[0] if row else None
    out["lexize"] = lex
    found = conn.execute(
        "SELECT to_tsvector('pl_unaccent', 'Oddział w Łodzi') "
        "@@ websearch_to_tsquery('pl_unaccent', 'lodz')"
    ).fetchone()
    out["lodz_finds_lodzi"] = bool(found and found[0])
    today = (
        "(to_tsvector('public.pl', %(t)s) @@ websearch_to_tsquery('public.pl', %(q)s) OR "
        "to_tsvector('english', %(t)s) @@ websearch_to_tsquery('english', %(q)s) OR "
        "to_tsvector('public.simple_unaccent', %(t)s) "
        "@@ websearch_to_tsquery('public.simple_unaccent', %(q)s))"
    )
    with_unaccented = (
        f"({today} OR to_tsvector('pl_unaccent', %(t)s) "
        "@@ websearch_to_tsquery('pl_unaccent', %(q)s))"
    )

    def matches(pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
        rows = []
        for t, q in pairs:
            hit = conn.execute(f"SELECT {today}, {with_unaccented}", {"t": t, "q": q}).fetchone()
            assert hit is not None
            rows.append(
                {"text": t, "query": q, "today": bool(hit[0]), "with_unaccented": bool(hit[1])}
            )
        return rows

    confusions, gains = matches(CONFUSIONS), matches(GAINS)
    out["confusions"] = {
        "new_false_matches": sum(r["with_unaccented"] and not r["today"] for r in confusions),
        "of": len(confusions),
        "rows": confusions,
    }
    out["gains"] = {
        "new_true_matches": sum(r["with_unaccented"] and not r["today"] for r in gains),
        "of": len(gains),
        "rows": gains,
    }
    original = dict_dir / "pl_pl.dict"
    if original.is_file():
        stems: dict[str, set[str]] = defaultdict(set)
        total = 0
        for line in original.read_text(encoding="utf-8").splitlines()[1:]:
            stem = line.split("/", 1)[0].strip().lower()
            if stem:
                total += 1
                stems[fold(stem)].add(stem)
        colliding = {k: v for k, v in stems.items() if len(v) > 1}
        out["dictionary"] = {
            "stems": total,
            "folded_keys_with_several_stems": len(colliding),
            "stems_involved": sum(len(v) for v in colliding.values()),
            "share_of_stems_pct": round(
                100 * sum(len(v) for v in colliding.values()) / max(total, 1), 2
            ),
            "pairs_found_in_dictionary": [
                "/".join(sorted(stems[fold(a)]))
                for a, _ in PAIRS
                if len(stems.get(fold(a), ())) > 1
            ],
        }
    sample = conn.execute("SELECT count(*) FROM srch_parts").fetchone()[0]  # type: ignore[index]
    conn.execute(
        "CREATE TEMP TABLE un_sample AS SELECT to_tsvector('pl_unaccent', text_content) AS tsv "
        "FROM srch_parts TABLESAMPLE SYSTEM (20) REPEATABLE (7)"
    )
    rows, col = conn.execute("SELECT count(*), sum(pg_column_size(tsv)) FROM un_sample").fetchone()  # type: ignore[misc]
    conn.execute("CREATE INDEX ON un_sample USING gin (tsv)")
    gin = conn.execute(
        "SELECT pg_relation_size(i.indexrelid) FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indrelid "
        "WHERE c.relname = 'un_sample'"
    ).fetchone()[0]  # type: ignore[index]
    factor = sample / max(rows, 1)
    out["extra_space_estimate_mb"] = {
        "tsvector": mb(int(col * factor)),
        "gin": mb(int(gin * factor)),
        "sampled_parts": rows,
    }
    conn.rollback()
    return out


# --- main ----------------------------------------------------------------------------------------


def markdown(result: dict[str, Any]) -> str:
    lines = [f"# Search performance, {result['documents']} documents ({result['date']})", ""]
    c = result["corpus"]
    lines += [
        f"Corpus: {c['documents']} documents, {c['parts']} parts, "
        f"{c['chars'] / 1e6:.0f} M characters, "
        f"{c['drafts']} drafts, {c['emails']} e-mails. Hardware: {result['hardware']}.",
        "",
    ]
    ix = result["indexing"]
    lines += [
        "## Indexing",
        "",
        f"Full index (one process): {ix['seconds']} s, "
        f"{ix['documents_per_minute']} documents/min, {ix['parts_per_second']} parts/s.",
        "",
    ]
    lines += [
        "## Size",
        "",
        "```",
        json.dumps(result["sizes"], indent=2, ensure_ascii=False),
        "```",
        "",
    ]
    lines += [
        "## Queries (warm, ms)",
        "",
        "| Query | p50 | p95 | max | found | fallback |",
        "|---|---|---|---|---|---|",
    ]
    for label, s in result["queries"].items():
        lines.append(
            f"| {label} | {s['p50_ms']} | {s['p95_ms']} | {s['max_ms']} | {s['found']} "
            f"| {s['fallbacks']} |"
        )
    allq = result["all_queries"]
    lines += [
        "",
        f"All query runs: p50 {allq['p50_ms']} ms, p95 {allq['p95_ms']} ms, "
        f"max {allq['max_ms']} ms.",
        "",
    ]
    lines += ["## 10 concurrent searches", ""]
    for label, key in (
        ("new connections (first use loads the dictionary)", "concurrent_cold"),
        ("warm connections", "concurrent"),
    ):
        cc = result[key]
        lines.append(
            f"- {label}: p50 {cc['p50_ms']} ms, p95 {cc['p95_ms']} ms, max {cc['max_ms']} ms, "
            f"{cc['searches_per_second']} searches/s ({cc['n']} searches, {cc['threads']} threads)"
        )
    lines.append("")
    for label, plan in result["explain"].items():
        lines += [f"## EXPLAIN (ANALYZE, BUFFERS): {label}", "", "```", plan, "```", ""]
    if "unaccented" in result:
        lines += [
            "## Dictionary without diacritics",
            "",
            "```",
            json.dumps(result["unaccented"], indent=2, ensure_ascii=False),
            "```",
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", type=int, default=20000)
    parser.add_argument("--reps", type=int, default=10)
    parser.add_argument("--out", type=Path, default=Path("/out"))
    parser.add_argument("--dict-dir", type=Path, default=Path("/dict"))
    args = parser.parse_args()
    dsn = os.environ["PERF_DATABASE_URL"]
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=dsn.replace("postgresql://", "postgresql+psycopg://", 1),
        redis_url="redis://unused:6379/0",
        secret_key="perf-secret-key-0123456789abcdef0123",
        db_pool_size=12,
        db_max_overflow=0,
    )
    result: dict[str, Any] = {
        "documents": args.docs,
        "date": datetime.now(UTC).date().isoformat(),
        "hardware": f"{os.cpu_count()} CPU threads (test machine; production may differ)",
    }
    print(f"==> migrate, generate {args.docs} documents", flush=True)
    upgrade(settings)
    with psycopg.connect(dsn) as conn:
        started = time.perf_counter()
        corpus = generate(conn, args.docs)
        result["corpus"] = corpus.__dict__ | {"users": len(corpus.users)}
        result["generate_seconds"] = round(time.perf_counter() - started, 1)
    engine = make_engine(settings)
    factory = make_session_factory(engine)
    with factory() as db:
        versions = [r[0] for r in db.execute(text("SELECT version_id FROM ing_results"))]
    print(f"==> full index of {len(versions)} versions (one process)", flush=True)
    started = time.perf_counter()
    indexer.index_many(factory, versions)
    seconds = time.perf_counter() - started
    result["indexing"] = {
        "seconds": round(seconds, 1),
        "documents_per_minute": round(len(versions) / seconds * 60),
        "parts_per_second": round(corpus.parts / seconds),
    }
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("VACUUM ANALYZE")
        result["sizes"] = sizes(conn)
    backend = PostgresBackend(settings)
    print("==> queries", flush=True)
    cases = queries(corpus.samples)
    result["queries"], every = run_queries(factory, backend, args.reps, cases)
    result["all_queries"] = summary(every)
    print("==> EXPLAIN", flush=True)
    result["explain"] = {
        label: explain(engine, factory, backend, q)
        for label, q in (
            ("selective", cases[0][1]["query"]),
            ("typical", "przegląd urządzeń"),
            ("broad", "umowa"),
        )
    }
    print(
        "==> 10 concurrent searches (new connections, then the same connections again)", flush=True
    )
    # A new database connection loads the Polish dictionary on first use (about 27 MB, 0.3 to
    # 0.8 s): the first round shows that cost, the second the steady state of a warm pool.
    fresh = make_engine(settings)
    fresh_factory = make_session_factory(fresh)
    result["concurrent_cold"] = concurrent(fresh_factory, backend, cases, threads=10, per_thread=20)
    result["concurrent"] = concurrent(fresh_factory, backend, cases, threads=10, per_thread=20)
    fresh.dispose()
    if (args.dict_dir / "pl_unaccent.dict").is_file():
        print("==> dictionary without diacritics", flush=True)
        try:
            with psycopg.connect(dsn) as conn:
                result["unaccented"] = unaccented(dsn, args.dict_dir, conn)
        except psycopg.Error as exc:  # e.g. the transliterated affix file does not load
            result["unaccented"] = {"error": f"{type(exc).__name__} sqlstate={exc.sqlstate}"}
    engine.dispose()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"perf-{args.docs}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str)
    )
    (args.out / f"perf-{args.docs}.md").write_text(markdown(result))
    print(markdown(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
