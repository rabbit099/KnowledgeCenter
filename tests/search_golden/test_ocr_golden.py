"""Golden search tests on synthetic scans with defects (Phase 4 stage C, ADR-034).

Every scan of tests/fixtures/scans.py goes through the real pipeline: upload, text pass,
OCR pass (OCRmyPDF + Tesseract, as in ocr-worker), indexing, publication. Then a Reader searches
for words of the scans in other forms than written: inflected Polish forms, no diacritics,
English forms, a phrase. Every one must be found. Numbers (a phone number and a tax number)
are only asserted where OCR reads them correctly; the misses are the documented limitation
(docs/en/ingestion-and-ocr.md, "Search in OCR text"). Needs Tesseract: `make test-db`.
"""

from __future__ import annotations

import re
import shutil
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from baza_wiedzy.core import runtime
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.registry import load_registry
from baza_wiedzy.main import create_app
from baza_wiedzy.modules.documents.models import Document
from baza_wiedzy.modules.ingestion import pipeline, tasks
from fixtures.samples import text_pdf
from fixtures.scans import BASE_LINES, SCANS
from web_helpers import csrf, login, make_user

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs Tesseract (make test-db)"),
]

RESULT_LINK = re.compile(r'<h3 class="hit-title"><a href="/documents/([0-9a-f-]{36})">')
TEXT_LAYER = "warstwa-tekstu"  # the same lines as a real text PDF (ranking against OCR text)

# Queries in other forms than the scans contain (BASE_LINES); every scan with BASE_LINES must
# be found by each. The table scan has its own words.
WORD_QUERIES = {
    "protokoły": "inflected (Protokół)",
    "odbiór": "inflected (odbioru)",
    "urządzenie": "inflected (urządzeń)",
    "chłodniczy": "inflected (chłodniczych)",
    "chlodniczych": "without diacritics",
    "zazolc": "without diacritics (Zażółć)",
    "reports": "English plural (report)",
    "approve": "English form (approved)",
    '"protokół odbioru"': "phrase",
}
TABLE_QUERIES = {"zestawieniu": "inflected", "windy": "as written", "wymianie": "inflected"}
NUMBER_QUERIES = {"600 100 201": "phone number", "1234563218": "tax number without dashes"}

# Measured with Tesseract 5.5.0, Debian "fast" models (stage C): the scans on which OCR reads
# BOTH numbers correctly. On the others a digit or a separator is misread, so the number is
# not found: the documented limitation. A change of this set means OCR changed: update the docs.
NUMBERS_READ = {
    "01-czysty.pdf",
    "02-przekrzywiony.pdf",
    "04-rozmyty.pdf",
    "05-maly-druk.pdf",
    "07-obrocony.pdf",
    "08-niski-kontrast.pdf",
    "09-mieszany.pdf",
    "10-mail-ze-skanem.eml",
    "11-outlook-ze-skanem.msg",
}  # not 03-szum.pdf: with 6 % noise both numbers are misread (measured, stage C)


@dataclass
class Scans:
    reader: TestClient
    ids: dict[str, uuid.UUID]

    def found(self, query: str) -> list[str]:
        keys = {doc_id: key for key, doc_id in self.ids.items()}
        response: httpx.Response = self.reader.get("/search", params={"q": query})
        assert response.status_code == 200
        return [
            keys[uuid.UUID(m)] for m in RESULT_LINK.findall(response.text) if uuid.UUID(m) in keys
        ]


@pytest.fixture(scope="module")
def scans(migrated: Settings) -> Iterator[Scans]:
    settings = migrated.model_copy(update={"db_pool_size": 3, "db_max_overflow": 0})
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tasks, "enqueue", lambda *a, **k: None)  # both passes run below
        patch.setattr(tasks, "enqueue_ocr", lambda *a, **k: None)
        app = create_app(settings, setup_logging=False)
        worker_settings = settings.model_copy(update={"db_pool_size": 2, "db_max_overflow": 0})
        rt = runtime.build(worker_settings, load_registry(settings.enabled_modules))
        runtime.install(rt)
        try:
            owner = login(app, make_user(app, "publisher"))
            ids: dict[str, uuid.UUID] = {}
            files = [(scan.filename, scan.build()) for scan in SCANS]
            files.append((f"{TEXT_LAYER}.pdf", text_pdf(BASE_LINES)))
            for name, data in files:
                ids[name.rsplit(".", 1)[0]] = _upload_and_process(app, rt, owner, name, data)
            while pipeline.process_ocr(rt) != pipeline.Outcome.SKIPPED:
                pass  # every queued scan, fewest pages first
            for doc_id in ids.values():
                _publish(owner, doc_id)
            yield Scans(reader=login(app, make_user(app, "reader")), ids=ids)
        finally:
            runtime.install(None)
            rt.close()


def _upload_and_process(
    app: FastAPI, rt: runtime.Runtime, c: TestClient, name: str, data: bytes
) -> uuid.UUID:
    unique = f"Dokument {uuid.uuid4().hex[:8]}"  # neutral, unique title: content decides
    response: httpx.Response = c.post(
        "/documents/upload",
        files={"file": (name, data, "application/octet-stream")},
        data={"title": unique},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf(c, "/documents/upload")},
        follow_redirects=False,
    )
    assert response.status_code == 204, response.text
    doc_id = uuid.UUID(response.headers["hx-redirect"].split("/")[2].split("?")[0])
    with app.state.session_factory() as db:
        version_id = db.scalar(select(Document.latest_version_id).where(Document.id == doc_id))
    assert version_id is not None
    assert pipeline.process(rt, version_id) == pipeline.Outcome.DONE, name
    return doc_id


def _publish(c: TestClient, doc_id: uuid.UUID) -> None:
    response = c.post(
        f"/documents/{doc_id}/lifecycle",
        data={"action": "publish", "expected": "draft", "csrf_token": csrf(c, "/")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text[:300]


TEXT_SCANS = [s.filename.rsplit(".", 1)[0] for s in SCANS if s.filename != "06-tabela.pdf"]


@pytest.mark.parametrize("query", list(WORD_QUERIES), ids=list(WORD_QUERIES.values()))
def test_words_of_every_defective_scan_are_found(scans: Scans, query: str) -> None:
    found = set(scans.found(query))
    missing = [key for key in TEXT_SCANS if key not in found]
    assert not missing, f"{query!r} ({WORD_QUERIES[query]}): not found in {missing}"


@pytest.mark.parametrize("query", list(TABLE_QUERIES))
def test_words_of_the_table_scan_are_found(scans: Scans, query: str) -> None:
    assert "06-tabela" in scans.found(query)


@pytest.mark.parametrize("query", list(NUMBER_QUERIES), ids=list(NUMBER_QUERIES.values()))
def test_numbers_where_ocr_reads_them(scans: Scans, query: str) -> None:
    found = set(scans.found(query))
    read = {name.rsplit(".", 1)[0] for name in NUMBERS_READ}
    assert read <= found, f"{query!r}: now missing in {sorted(read - found)}"
    assert TEXT_LAYER in found


def test_the_text_layer_ranks_above_the_same_words_from_ocr(scans: Scans) -> None:
    found = scans.found("protokół odbioru urządzeń chłodniczych")
    assert found[0] == TEXT_LAYER, found[:3]
    assert set(TEXT_SCANS) <= set(found)


def test_results_label_ocr_text_for_a_reader(scans: Scans) -> None:
    page = scans.reader.get("/search", params={"q": "chłodniczych"}).text
    hits = page.split('<li class="hit">')[1:]
    by_doc = {
        uuid.UUID(m.group(1)): hit
        for hit in hits
        if (m := re.search(r'href="/documents/([0-9a-f-]{36})"', hit))
    }
    assert "tekst z OCR" in by_doc[scans.ids["01-czysty"]]
    assert "tekst z OCR" not in by_doc[scans.ids[TEXT_LAYER]]
