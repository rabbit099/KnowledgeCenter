"""Golden search tests on the synthetic corpus (tests/fixtures/search_corpus.py, ADR-026/027).

Every corpus file goes through the real pipeline: upload through the web application,
extraction (pipeline.process), indexing by the document.version.extracted event, publication
or archiving through the lifecycle form. Every query of the corpus is then run through the
search page as the user it names, and the documents found are compared with the expectations
(must / must not / first). Deterministic: fixed texts, fixed dates, ties ordered by id. Other
documents in the shared test database may appear in results; only corpus documents are checked.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update

from baza_wiedzy.cli import main
from baza_wiedzy.core import runtime
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.registry import load_registry
from baza_wiedzy.main import create_app
from baza_wiedzy.modules.documents.models import Document, Version
from baza_wiedzy.modules.ingestion import pipeline, tasks
from baza_wiedzy.modules.search.models import SearchDocument, SearchPart
from fixtures.search_corpus import ANNA, ARCHIVED, BARTEK, DOCS, PUBLISHED, QUERIES, GoldenQuery
from web_helpers import csrf, login, make_user

pytestmark = pytest.mark.integration

RESULT_LINK = re.compile(r'<h3 class="hit-title"><a href="/documents/([0-9a-f-]{36})">')


@dataclass
class Corpus:
    app: FastAPI
    rt: runtime.Runtime
    clients: dict[str, TestClient]
    ids: dict[str, uuid.UUID]  # corpus key -> document id

    def search(self, who: str, **params: str | list[str]) -> httpx.Response:
        response: httpx.Response = self.clients[who].get("/search", params=params)
        assert response.status_code == 200, response.text[:300]
        return response

    def found(self, who: str, **params: str | list[str]) -> list[str]:
        keys = {doc_id: key for key, doc_id in self.ids.items()}
        page = self.search(who, **params).text
        return [keys[uuid.UUID(m)] for m in RESULT_LINK.findall(page) if uuid.UUID(m) in keys]


def _upload(c: TestClient, name: str, data: bytes, title: str) -> tuple[uuid.UUID, uuid.UUID]:
    response: httpx.Response = c.post(
        "/documents/upload",
        files={"file": (name, data, "application/octet-stream")},
        data={"title": title},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf(c, "/documents/upload")},
        follow_redirects=False,
    )
    assert response.status_code == 204, response.text
    doc_id = uuid.UUID(response.headers["hx-redirect"].split("/")[2].split("?")[0])
    with c.app.state.session_factory() as db:  # type: ignore[attr-defined]
        version_id = db.scalar(select(Document.latest_version_id).where(Document.id == doc_id))
    assert version_id is not None
    return doc_id, version_id


def _act(c: TestClient, doc_id: uuid.UUID, action: str, expected: str) -> None:
    response = c.post(
        f"/documents/{doc_id}/lifecycle",
        data={"action": action, "expected": expected, "csrf_token": csrf(c, "/")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text[:300]


@pytest.fixture(scope="module")
def corpus(migrated: Settings) -> Iterator[Corpus]:
    settings = migrated.model_copy(update={"db_pool_size": 3, "db_max_overflow": 0})
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tasks, "enqueue", lambda *a, **k: None)  # processed synchronously below
        app = create_app(settings, setup_logging=False)
        # Exactly the worker's pool (docker-compose.yml): the outbox delivery holds one
        # connection while the indexing handler needs a second (regression: with 1 the event
        # was never delivered).
        worker_settings = settings.model_copy(update={"db_pool_size": 2, "db_max_overflow": 0})
        rt = runtime.build(worker_settings, load_registry(settings.enabled_modules))
        runtime.install(rt)
        try:
            users = {
                ANNA: make_user(app, "publisher"),
                BARTEK: make_user(app, "publisher"),
                "reader": make_user(app, "reader"),
                "admin": make_user(app, "admin"),
            }
            clients = {who: login(app, user) for who, user in users.items()}
            ids: dict[str, uuid.UUID] = {}
            for doc in DOCS:
                owner = clients[doc.owner]
                doc_id, version_id = _upload(owner, doc.filename, doc.build(), doc.title)
                assert pipeline.process(rt, version_id) == pipeline.Outcome.DONE, doc.key
                with rt.session_factory.begin() as db:
                    db.execute(
                        update(Version)
                        .where(Version.id == version_id)
                        .values(uploaded_at=datetime.fromisoformat(doc.date).replace(tzinfo=UTC))
                    )
                if doc.state in (PUBLISHED, ARCHIVED):
                    _act(owner, doc_id, "publish", "draft")
                if doc.state == ARCHIVED:
                    _act(owner, doc_id, "archive", "published")
                ids[doc.key] = doc_id
            yield Corpus(app=app, rt=rt, clients=clients, ids=ids)
        finally:
            runtime.install(None)
            rt.close()


def _case_id(case: GoldenQuery) -> str:
    return f"{case.query}|{case.as_user}{'|archived' if case.archived else ''}"


@pytest.mark.parametrize("case", QUERIES, ids=[_case_id(c) for c in QUERIES])
def test_golden_query(corpus: Corpus, case: GoldenQuery) -> None:
    params: dict[str, str | list[str]] = {"q": case.query}
    if case.archived:
        params["archived"] = "1"
    found = corpus.found(case.as_user, **params)
    assert case.must <= set(found), f"{case.note}: missing {sorted(case.must - set(found))}"
    assert not case.must_not & set(found), f"{case.note}: unexpected {case.must_not & set(found)}"
    if case.top is not None:
        assert found and found[0] == case.top, f"{case.note}: first is {found[:1]}"


def test_every_corpus_document_is_indexed(corpus: Corpus) -> None:
    with corpus.rt.session_factory() as db:
        indexed = set(
            db.scalars(
                select(SearchDocument.document_id).where(
                    SearchDocument.document_id.in_(corpus.ids.values())
                )
            )
        )
    assert indexed == set(corpus.ids.values())


def test_snippet_is_escaped_highlighted_and_located(corpus: Corpus) -> None:
    page = corpus.search("reader", q="klimatyzatory").text
    assert "załącznik: protokol.pdf › strona 1" in page
    assert "<mark>klimatyzatorów</mark>" in page


def test_html_in_a_document_is_never_rendered(corpus: Corpus) -> None:
    owner = corpus.clients[ANNA]
    text = b"Instrukcja <b>znacznikowyzwrot</b> <script>alert('xss')</script> koniec."
    doc_id, version_id = _upload(owner, "xss.txt", text, "Test znaczników")
    assert pipeline.process(corpus.rt, version_id) == pipeline.Outcome.DONE
    _act(owner, doc_id, "publish", "draft")
    page = corpus.search("reader", q="znacznikowyzwrot").text
    snippet = page[page.index('<p class="snippet">') :].split("</p>", 1)[0]
    assert "<script>alert" not in page and "<b>" not in snippet
    # ts_headline drops markup tokens itself; whatever text remains is escaped by us.
    assert "<mark>znacznikowyzwrot</mark>" in snippet and "alert(&#39;xss&#39;)" in snippet


def test_sorting_by_date_and_relevance(corpus: Corpus) -> None:
    by_date = corpus.found("reader", q="faktura", sort="date")
    assert by_date.index("faktura-0043") < by_date.index("faktura-0042")
    assert corpus.found("reader", q="FV/2026/11/0042")[0] == "faktura-0042"


def test_filters(corpus: Corpus) -> None:
    pdf_only = corpus.found("reader", q="faktura", kind="pdf")
    assert "faktura-0042" in pdf_only and "faktura-0043" not in pdf_only
    mails = corpus.found("reader", q="przegląd", kind="email")
    assert mails and all(k.startswith("mail-") for k in mails)
    assert corpus.found("reader", q="faktura", **{"from": "2026-11-04"}) == ["faktura-0043"]
    assert "faktura-0043" not in corpus.found("reader", q="faktura", to="2026-11-03")
    with corpus.rt.session_factory() as db:
        bartek = db.scalar(select(Document.owner_id).where(Document.id == corpus.ids["aneks"]))
    by_bartek = corpus.found("reader", q="umowa", author=str(bartek))
    assert by_bartek == ["aneks"]
    english = corpus.found("reader", q="backups", lang="en")
    assert english == ["backup-policy"]
    assert "backup-policy" not in corpus.found("reader", q="backups", lang="pl")


def test_count_and_pagination_keep_the_parameters(corpus: Corpus) -> None:
    page = corpus.search("reader", q="umowa", sort="date").text
    assert re.search(r"Znaleziono \d+ dokument", page)
    assert 'role="search"' in page and 'for="search-q"' in page


def test_query_length_limits(corpus: Corpus) -> None:
    assert "Wpisz co najmniej 2 znaki" in corpus.search("reader", q="a").text
    assert "Zapytanie jest za długie" in corpus.search("reader", q="x" * 201).text


def test_drafts_and_archived_marked_for_those_who_see_them(corpus: Corpus) -> None:
    assert "szkic" in corpus.search(ANNA, q="regulamin").text
    assert corpus.found("admin", q="regulamin") == ["szkic-regulamin"]
    reader_page = corpus.search("reader", q="cennik", archived="1").text
    assert "Pokaż zarchiwizowane" not in reader_page  # a Reader has no such filter


def test_search_phrases_never_reach_the_log(
    corpus: Corpus, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    phrase = "tajnafraza" + uuid.uuid4().hex[:8]
    corpus.search("reader", q=phrase)
    corpus.search("reader", q=f'"{phrase} umowa" -{phrase}x', sort="date", kind="pdf")
    # The test client itself logs request URLs (logger httpx); the application must not.
    app_log = "\n".join(r.getMessage() for r in caplog.records if r.name != "httpx")
    assert phrase not in app_log
    assert re.search(r"search: results=0 page=1 sort=date filters=kind fallback=no", app_log)


def test_time_limit_falls_back_or_explains(corpus: Corpus, migrated: Settings) -> None:
    slow = create_app(
        migrated.model_copy(update={"db_pool_size": 2, "search_timeout_ms": 1}),
        setup_logging=False,
    )
    c = login(slow, make_user(slow, "reader"))
    response = c.get("/search", params={"q": "umowa"})
    assert response.status_code == 200
    assert (
        "Wyszukiwanie trwało zbyt długo" in response.text
        or "wyniki są posortowane według daty" in response.text
        or "Znaleziono" in response.text  # the machine was simply fast enough
    )


def test_check_and_reindex_commands(
    corpus: Corpus,
    migrated: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated.database_url)
    monkeypatch.setenv("REDIS_URL", migrated.redis_url)
    monkeypatch.setenv("STORAGE_DIR", migrated.storage_dir)
    monkeypatch.setenv("SECRET_KEY", migrated.secret_key)
    assert main(["reindex-search", "--missing"]) == 0
    assert main(["search-check"]) == 0
    assert "Search index OK." in capsys.readouterr().out
    victim = corpus.ids["kontakt"]
    with corpus.rt.session_factory.begin() as db:
        db.execute(delete(SearchPart).where(SearchPart.document_id == victim))
        db.execute(delete(SearchDocument).where(SearchDocument.document_id == victim))
    assert corpus.found("reader", q="603388100") == []
    assert main(["search-check"]) == 1
    out = capsys.readouterr().out
    assert "not indexed: 1" in out and "reindex-search --missing" in out
    assert main(["reindex-search", "--missing"]) == 0
    assert "Indexed 1 version(s)" in capsys.readouterr().out
    assert corpus.found("reader", q="603388100") == ["kontakt"]
    assert main(["reindex-search", "--document", str(victim)]) == 0
    assert main(["reindex-search"]) == 1  # nothing chosen
    assert main(["search-check"]) == 0


def test_publication_and_archiving_take_effect_without_reindexing(corpus: Corpus) -> None:
    owner = corpus.clients[ANNA]
    word = "natychmiastowosc" + uuid.uuid4().hex[:6]
    doc_id, version_id = _upload(owner, "nowy.txt", f"Nowy dokument {word}.".encode(), "")
    assert pipeline.process(corpus.rt, version_id) == pipeline.Outcome.DONE

    def reader_finds() -> bool:
        return f"/documents/{doc_id}" in corpus.search("reader", q=word).text

    assert not reader_finds()  # draft
    assert f"/documents/{doc_id}" in corpus.search(ANNA, q=word).text  # the owner finds it
    _act(owner, doc_id, "publish", "draft")
    assert reader_finds()
    _act(owner, doc_id, "archive", "published")
    assert not reader_finds()
    assert f"/documents/{doc_id}" in corpus.search(ANNA, q=word, archived="1").text


def test_indexing_twice_changes_nothing(corpus: Corpus) -> None:
    from baza_wiedzy.modules.search import indexer

    version_id = None
    with corpus.rt.session_factory() as db:
        version_id = db.scalar(
            select(Document.latest_version_id).where(Document.id == corpus.ids["mail-klimatyzacja"])
        )
        before = db.scalar(
            select(func.count()).select_from(SearchPart).where(SearchPart.version_id == version_id)
        )
    assert version_id is not None and before
    for _ in range(2):  # the event is delivered at least once
        indexer.on_extracted(
            {"document_id": str(corpus.ids["mail-klimatyzacja"]), "version_id": str(version_id)}
        )
    with corpus.rt.session_factory() as db:
        after = db.scalar(
            select(func.count()).select_from(SearchPart).where(SearchPart.version_id == version_id)
        )
    assert after == before
    assert corpus.found("reader", q="PR-88/2026") == ["mail-klimatyzacja"]


def test_home_page_has_the_search_form(corpus: Corpus) -> None:
    page = corpus.clients["reader"].get("/").text
    assert 'action="/search"' in page and 'for="home-q"' in page
    assert "w przygotowaniu" not in page
    assert 'href="/search"' in page  # menu item
