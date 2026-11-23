"""OCR in its own queue and worker (Phase 4 stage B, ADR-033): automatic OCR of small scans,
large documents on hold until "Uruchom OCR", time limit -> "OCR nieudany" and retry,
cancellation of a running OCR, ordinary uploads not waiting for OCR, permissions, audit,
recovery at worker start, the CLI, and no recognised text in the logs.

Real Postgres, Redis and Tesseract (make test-db); two in-process Dramatiq workers, one per
queue, like the services worker and ocr-worker. Synthetic scans only.
"""

from __future__ import annotations

import logging
import shutil
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import dramatiq
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from baza_wiedzy.cli import main
from baza_wiedzy.core import runtime
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.models import AuditEntry
from baza_wiedzy.core.queue import setup_broker
from baza_wiedzy.core.registry import load_registry
from baza_wiedzy.main import create_app
from baza_wiedzy.modules.documents.models import Document, Version
from baza_wiedzy.modules.documents.processing import OCR_INTERRUPTED
from baza_wiedzy.modules.ingestion import ocr, startup
from baza_wiedzy.modules.ingestion.pipeline import OCR_TIMEOUT
from baza_wiedzy.modules.ingestion.services import api
from fixtures.scans import BASE_LINES, mixed_pdf, page_image, scan_pdf
from web_helpers import csrf, login, make_user

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs Tesseract (make test-db)"),
]

LIMITS = {"ocr_auto_max_pages": 1, "ocr_timeout_seconds": 120}
SLOW = [sys.executable, "-c", "import time; time.sleep(120)"]


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(
        settings.model_copy(update={"db_pool_size": 3, **LIMITS}), setup_logging=False
    )


@pytest.fixture
def rt(settings: Settings) -> Iterator[runtime.Runtime]:
    worker_settings = settings.model_copy(update={"db_pool_size": 4, **LIMITS})
    current = runtime.build(worker_settings, load_registry(worker_settings.enabled_modules))
    runtime.install(current)
    yield current
    runtime.install(None)
    current.close()


@pytest.fixture
def broker(rt: runtime.Runtime) -> Iterator[dramatiq.Broker]:
    current = setup_broker(rt.settings, rt.registry)
    for queue in ("ingestion", "ocr"):  # nothing left over from other tests
        current.flush(queue)
    yield current
    current.close()


@pytest.fixture
def ingest_worker(app: FastAPI, broker: dramatiq.Broker) -> Iterator[dramatiq.Worker]:
    worker = dramatiq.Worker(broker, queues={"ingestion"}, worker_threads=1, worker_timeout=100)
    worker.start()
    yield worker
    worker.stop()


@pytest.fixture
def ocr_worker(app: FastAPI, broker: dramatiq.Broker) -> Iterator[dramatiq.Worker]:
    worker = dramatiq.Worker(broker, queues={"ocr"}, worker_threads=1, worker_timeout=100)
    worker.start()
    yield worker
    worker.stop()


def upload(c: TestClient, name: str, data: bytes) -> tuple[uuid.UUID, uuid.UUID]:
    response: httpx.Response = c.post(
        "/documents/upload",
        files={"file": (name, data, "application/octet-stream")},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf(c, "/documents/upload")},
        follow_redirects=False,
    )
    assert response.status_code == 204, response.text
    doc_id = uuid.UUID(response.headers["hx-redirect"].split("/")[2].split("?")[0])
    with c.app.state.session_factory() as db:  # type: ignore[attr-defined]
        document = db.get(Document, doc_id)
        assert document is not None and document.latest_version_id is not None
        return doc_id, document.latest_version_id


def version_of(rt: runtime.Runtime, version_id: uuid.UUID) -> Version:
    with rt.session_factory() as db:
        version = db.get(Version, version_id)
        assert version is not None
        return version


def wait_until(
    rt: runtime.Runtime,
    version_id: uuid.UUID,
    check: Callable[[Version], bool],
    timeout: float = 90,
) -> Version:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        version = version_of(rt, version_id)
        if check(version):
            return version
        time.sleep(0.2)
    version = version_of(rt, version_id)
    raise AssertionError(
        f"timeout: processing={version.processing_status} ocr={version.ocr_status}"
    )


def ocr_is(*states: str) -> Callable[[Version], bool]:
    return lambda v: v.processing_status in ("needs_ocr", "done") and v.ocr_status in states


def unique_scan(pages: int = 1) -> bytes:
    """Image-only pages; a unique line keeps the content unique (no duplicate hold)."""
    tag = uuid.uuid4().hex[:8]
    return scan_pdf(*[page_image([*BASE_LINES, f"Kod {tag}"]) for _ in range(pages)])


def unique_mixed() -> bytes:
    tag = uuid.uuid4().hex[:8]
    return mixed_pdf([f"Strona tekstowa {tag}: umowa najmu."], page_image(BASE_LINES))


def audit(rt: runtime.Runtime, action: str, doc_id: uuid.UUID) -> list[AuditEntry]:
    with rt.session_factory() as db:
        return list(
            db.scalars(
                select(AuditEntry).where(
                    AuditEntry.action == action, AuditEntry.entity_id == str(doc_id)
                )
            )
        )


def post(c: TestClient, url: str, page: str) -> httpx.Response:
    response: httpx.Response = c.post(
        url, data={"csrf_token": csrf(c, page)}, follow_redirects=False
    )
    return response


def test_a_small_scan_is_recognised_automatically_in_the_ocr_queue(
    app: FastAPI,
    rt: runtime.Runtime,
    ingest_worker: dramatiq.Worker,
    ocr_worker: dramatiq.Worker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    c = login(app, make_user(app, "publisher"))
    doc_id, version_id = upload(c, "skan.pdf", unique_scan())
    version = wait_until(rt, version_id, lambda v: v.processing_status == "done")
    assert (version.ocr_status, version.ocr_pages, version.ocr_applied) == ("done", 1, True)
    with rt.session_factory() as db:
        stored = api.parts_of(db, version_id)
    assert [(p.page_no, p.needs_ocr, p.ocr_applied) for p in stored] == [(1, False, True)]
    page = c.get(f"/documents/{doc_id}").text
    assert "tekst z OCR" in page and "OCR w kolejce" not in page
    assert f"/documents/{doc_id}" in c.get("/search", params={"q": "chłodniczych"}).text
    assert audit(rt, "document.ocr_request", doc_id) == []  # automatic: nobody asked
    assert "ocr: done version=" in caplog.text
    for secret in ("odbioru", "chłodniczych", "approved"):
        assert secret not in caplog.text.lower()  # recognised text never in the log


def test_a_large_scan_waits_on_hold_until_started(
    app: FastAPI,
    rt: runtime.Runtime,
    ingest_worker: dramatiq.Worker,
    ocr_worker: dramatiq.Worker,
) -> None:
    c = login(app, make_user(app, "publisher"))
    doc_id, version_id = upload(c, "duzy.pdf", unique_scan(pages=2))  # auto limit: 1 page
    version = wait_until(rt, version_id, ocr_is("on_hold"))
    assert (version.processing_status, version.ocr_pages) == ("needs_ocr", 2)
    page = c.get(f"/documents/{doc_id}").text
    assert "OCR wstrzymany (duży dokument)" in page and "Dokument ma 2 zeskanowane strony" in page
    assert "Uruchom OCR" in page
    time.sleep(1)
    assert version_of(rt, version_id).ocr_status == "on_hold"  # nothing starts by itself
    response = post(c, f"/ingestion/versions/{version_id}/ocr", f"/documents/{doc_id}")
    assert response.status_code == 303
    assert response.headers["location"] == f"/documents/{doc_id}?done=ocr_request"
    version = wait_until(rt, version_id, lambda v: v.processing_status == "done")
    assert version.ocr_status == "done" and version.ocr_applied
    entries = audit(rt, "document.ocr_request", doc_id)
    assert len(entries) == 1
    assert entries[0].details == {"version_id": str(version_id), "pages": 2, "source": "button"}


def test_time_limit_gives_ocr_failed_and_start_again_retries(
    app: FastAPI,
    rt: runtime.Runtime,
    ingest_worker: dramatiq.Worker,
    ocr_worker: dramatiq.Worker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ocr, "command", lambda *a, **k: SLOW)
    rt.settings = rt.settings.model_copy(update={"ocr_timeout_seconds": 2})
    c = login(app, make_user(app, "publisher"))
    doc_id, version_id = upload(c, "wolny.pdf", unique_mixed())
    started = time.monotonic()
    version = wait_until(rt, version_id, ocr_is("failed"), timeout=30)
    assert time.monotonic() - started < 25  # killed at the limit, not after the 120 s sleep
    assert version.processing_status == "needs_ocr" and version.ocr_error == OCR_TIMEOUT
    with rt.session_factory() as db:
        stored = api.parts_of(db, version_id)
    assert [(p.page_no, p.needs_ocr) for p in stored] == [(1, False), (2, True)]  # text kept
    page = c.get(f"/documents/{doc_id}").text
    assert "OCR nieudany" in page and "OCR trwał dłużej niż limit czasu" in page
    assert "Uruchom OCR ponownie" in page
    assert "Przetwórz ponownie" in c.get(f"/ingestion/versions/{version_id}/parts").text

    monkeypatch.undo()
    rt.settings = rt.settings.model_copy(update={"ocr_timeout_seconds": 120})
    assert post(c, f"/ingestion/versions/{version_id}/ocr", "/").status_code == 303
    version = wait_until(rt, version_id, lambda v: v.processing_status == "done")
    assert version.ocr_status == "done" and version.ocr_error is None


def test_cancel_stops_a_running_ocr_and_txt_uploads_do_not_wait(
    app: FastAPI,
    rt: runtime.Runtime,
    ingest_worker: dramatiq.Worker,
    ocr_worker: dramatiq.Worker,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(ocr, "command", lambda *a, **k: SLOW)
    c = login(app, make_user(app, "publisher"))
    doc_id, version_id = upload(c, "dlugi.pdf", unique_mixed())
    wait_until(rt, version_id, ocr_is("running"), timeout=30)

    # An ordinary upload while the long OCR runs: the ingestion worker handles it at once.
    started = time.monotonic()
    _, txt_version = upload(c, "notatka.txt", f"Notatka {uuid.uuid4()}\n".encode())
    wait_until(rt, txt_version, lambda v: v.processing_status == "done", timeout=15)
    assert time.monotonic() - started < 15
    assert version_of(rt, version_id).ocr_status == "running"

    page = c.get(f"/documents/{doc_id}").text
    assert "OCR w toku" in page and "Anuluj OCR" in page
    response = post(c, f"/ingestion/versions/{version_id}/ocr/cancel", f"/documents/{doc_id}")
    assert response.headers["location"] == f"/documents/{doc_id}?done=ocr_cancel"
    assert version_of(rt, version_id).ocr_status == "on_hold"
    deadline = time.monotonic() + 15  # the OCR worker notices within POLL_SECONDS
    while "ocr: result discarded" not in caplog.text and time.monotonic() < deadline:
        time.sleep(0.2)
    assert "ocr: result discarded" in caplog.text
    version = version_of(rt, version_id)
    assert (version.processing_status, version.ocr_status) == ("needs_ocr", "on_hold")
    assert len(audit(rt, "document.ocr_cancel", doc_id)) == 1


def test_start_and_cancel_permissions(
    app: FastAPI, rt: runtime.Runtime, ingest_worker: dramatiq.Worker
) -> None:
    owner = make_user(app, "publisher")
    c = login(app, owner)
    doc_id, version_id = upload(c, "macierz.pdf", unique_scan(pages=2))
    wait_until(rt, version_id, ocr_is("on_hold"))
    start = f"/ingestion/versions/{version_id}/ocr"
    cancel = f"{start}/cancel"
    other = login(app, make_user(app, "publisher"))
    reader = login(app, make_user(app, "reader"))
    admin = login(app, make_user(app, "admin"))
    # A draft: invisible to others (404), as on the document page.
    assert post(other, start, "/").status_code == 404
    assert post(reader, start, "/").status_code == 404
    assert c.post(start, follow_redirects=False).status_code == 403  # no CSRF token
    with app.state.session_factory.begin() as db:
        document = db.get(Document, doc_id)
        assert document is not None
        document.status, document.published_version_id = "published", version_id
    # Published: visible, but only the owner and documents.edit_any may act (403).
    assert post(other, start, "/").status_code == 403
    assert post(reader, start, "/").status_code == 403
    assert "Uruchom OCR" not in reader.get(f"/documents/{doc_id}").text
    assert post(c, start, "/").status_code == 303  # owner (documents.upload)
    assert post(other, cancel, "/").status_code == 403
    assert post(admin, cancel, "/").status_code == 303  # documents.edit_any
    assert version_of(rt, version_id).ocr_status == "on_hold"
    assert post(admin, start, "/").status_code == 303
    assert version_of(rt, version_id).ocr_status == "queued"
    assert len(audit(rt, "document.ocr_request", doc_id)) == 2
    assert len(audit(rt, "document.ocr_cancel", doc_id)) == 1
    # Nothing to start twice: the state is shown as unchanged.
    again = post(admin, start, "/")
    assert again.headers["location"].endswith("?done=ocr_unchanged")


def test_worker_start_fails_interrupted_ocr_and_resends_queued(
    app: FastAPI,
    rt: runtime.Runtime,
    ingest_worker: dramatiq.Worker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = login(app, make_user(app, "publisher"))
    _, interrupted = upload(c, "przerwany.pdf", unique_scan(pages=2))
    _, waiting = upload(c, "czeka.pdf", unique_scan(pages=2))
    for version_id in (interrupted, waiting):
        wait_until(rt, version_id, ocr_is("on_hold"))
    old = datetime.now(UTC) - timedelta(hours=3)
    with rt.session_factory.begin() as db:
        first = db.get(Version, interrupted)
        second = db.get(Version, waiting)
        assert first is not None and second is not None
        first.ocr_status, first.ocr_started_at = "running", old
        second.ocr_status, second.ocr_requested_at = "queued", old
    sent: list[int] = []
    monkeypatch.setattr(startup, "enqueue_ocr", lambda settings, count=1: sent.append(count))
    result = startup.recover(rt)
    assert result.ocr_failed >= 1 and sent and sent[0] >= 1
    first_after = version_of(rt, interrupted)
    assert (first_after.ocr_status, first_after.ocr_error) == ("failed", OCR_INTERRUPTED)
    assert version_of(rt, waiting).ocr_status == "queued"


def test_cli_ocr_pending_and_by_id(
    settings: Settings,
    app: FastAPI,
    rt: runtime.Runtime,
    ingest_worker: dramatiq.Worker,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("REDIS_URL", settings.redis_url)
    monkeypatch.setenv("STORAGE_DIR", settings.storage_dir)
    monkeypatch.setenv("OCR_AUTO_MAX_PAGES", "1")
    c = login(app, make_user(app, "publisher"))
    small_doc, small = upload(c, "dwie.pdf", unique_scan(pages=2))
    big_doc, big = upload(c, "trzy.pdf", unique_scan(pages=3))
    for version_id in (small, big):
        wait_until(rt, version_id, ocr_is("on_hold"))
    with rt.session_factory.begin() as db:  # like a version processed before stage B
        legacy = db.get(Version, small)
        assert legacy is not None
        legacy.ocr_status, legacy.ocr_pages = "none", None

    assert main(["ocr"]) == 1
    assert main(["ocr", "--pending", str(big_doc)]) == 1
    capsys.readouterr()
    assert main(["ocr", "--pending", "--max-pages", "2"]) == 0
    out = capsys.readouterr().out
    assert "Queued for OCR:" in out and "Left out (more scanned pages than --max-pages 2)" in out
    assert version_of(rt, small).ocr_status == "queued"
    assert version_of(rt, small).ocr_pages == 2  # counted from the extracted parts
    assert version_of(rt, big).ocr_status == "on_hold"
    assert main(["ocr", str(big_doc)]) == 0
    assert version_of(rt, big).ocr_status == "queued"
    entries = audit(rt, "document.ocr_request", big_doc)
    assert entries[-1].actor_label == "cli" and entries[-1].details["source"] == "cli"
    assert audit(rt, "document.ocr_request", small_doc)[-1].details["pages"] == 2
    assert main(["ocr", str(uuid.uuid4())]) == 1


def test_reprocess_during_ocr_decides_again(
    app: FastAPI, rt: runtime.Runtime, ingest_worker: dramatiq.Worker
) -> None:
    c = login(app, make_user(app, "publisher"))
    doc_id, version_id = upload(c, "ponownie.pdf", unique_scan(pages=2))
    wait_until(rt, version_id, ocr_is("on_hold"))
    assert post(c, f"/ingestion/versions/{version_id}/ocr", "/").status_code == 303
    assert version_of(rt, version_id).ocr_status == "queued"  # no OCR worker in this test
    response = post(c, f"/ingestion/versions/{version_id}/reprocess", f"/documents/{doc_id}")
    assert response.status_code == 303
    version = wait_until(rt, version_id, ocr_is("on_hold"))
    assert version.processing_attempts == 1  # a fresh text pass decided "on hold" again
