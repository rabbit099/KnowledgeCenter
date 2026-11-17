"""Ingestion end to end: upload -> Dramatiq worker on Redis -> parts in PostgreSQL.

Also: idempotence, permanent and transient errors, stuck recovery, the outbox, startup work,
"Process again" (UI permissions, audit, CLI).
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import dramatiq
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from baza_wiedzy.cli import main
from baza_wiedzy.core import runtime
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.models import AuditEntry
from baza_wiedzy.core.events import DOCUMENT_VERSION_EXTRACTED, EventBus
from baza_wiedzy.core.queue import setup_broker
from baza_wiedzy.core.registry import load_registry
from baza_wiedzy.main import create_app
from baza_wiedzy.modules.documents import processing as documents_processing
from baza_wiedzy.modules.documents.models import Document, EmailMetadata, Version
from baza_wiedzy.modules.ingestion import outbox, pipeline, startup, tasks
from baza_wiedzy.modules.ingestion.extractors.base import TransientError
from baza_wiedzy.modules.ingestion.models import OutboxEntry, Part
from baza_wiedzy.modules.ingestion.services import api
from fixtures.msg import Attachment, Msg, msg_bytes
from fixtures.samples import (
    ENGLISH_TEXT,
    POLISH_TEXT,
    doc_bytes,
    docx_bytes,
    eml_bytes,
    encrypted_pdf,
    text_pdf,
)
from web_helpers import csrf, login, make_user

pytestmark = pytest.mark.integration

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FINAL = ("done", "failed", "needs_ocr")
EXTRACTED: list[Mapping[str, Any]] = []  # events received by the worker-side event bus
SENT: list[tuple[str, int]] = []  # (version id, delay) captured instead of enqueueing


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(
        settings.model_copy(update={"db_pool_size": 3, "max_upload_mb": 1}), setup_logging=False
    )


@pytest.fixture
def rt(settings: Settings) -> Iterator[runtime.Runtime]:
    worker_settings = settings.model_copy(update={"db_pool_size": 2})
    current = runtime.build(worker_settings, load_registry(worker_settings.enabled_modules))
    EXTRACTED.clear()
    current.events.subscribe(DOCUMENT_VERSION_EXTRACTED, EXTRACTED.append)
    runtime.install(current)
    yield current
    runtime.install(None)
    current.close()


@pytest.fixture
def worker(app: FastAPI, rt: runtime.Runtime) -> Iterator[dramatiq.Worker]:
    broker = setup_broker(rt.settings, rt.registry)
    worker = dramatiq.Worker(broker, worker_threads=1, worker_timeout=100)
    worker.start()
    yield worker
    worker.stop()
    broker.close()


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


def wait_final(rt: runtime.Runtime, version_id: uuid.UUID, timeout: float = 30) -> Version:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        version = version_of(rt, version_id)
        if version.processing_status in FINAL:
            return version
        time.sleep(0.2)
    raise AssertionError(f"version {version_id} not processed in {timeout} s")


def parts(rt: runtime.Runtime, version_id: uuid.UUID) -> list[api.PartView]:
    with rt.session_factory() as db:
        return api.parts_of(db, version_id)


def wait_for(condition: Callable[[], bool], timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


# --- end to end ------------------------------------------------------------------------------


def test_txt_and_eml_with_docx_attachment_end_to_end(
    app: FastAPI, rt: runtime.Runtime, worker: dramatiq.Worker
) -> None:
    c = login(app, make_user(app, "publisher"))
    _, txt_version = upload(c, "notatka.txt", POLISH_TEXT.encode("cp1250"))
    mail = eml_bytes(
        attachments=[
            ("umowa.docx", docx_bytes([ENGLISH_TEXT]), DOCX),
            ("logo.png", b"\x89PNG\r\n\x1a\n", "image/png"),
        ]
    )
    mail_doc, mail_version = upload(c, "przeglad.eml", mail)

    assert wait_final(rt, txt_version).processing_status == "done"
    [txt_part] = parts(rt, txt_version)
    assert (txt_part.text, txt_part.language, txt_part.part_type) == (POLISH_TEXT, "pl", "body")

    version = wait_final(rt, mail_version)
    assert version.processing_status == "done" and version.processing_attempts == 1
    body, attachment = parts(rt, mail_version)
    assert (body.text, body.language) == (POLISH_TEXT, "pl")
    assert (attachment.text, attachment.language) == (ENGLISH_TEXT, "en")
    assert attachment.origin == [{"type": "attachment", "name": "umowa.docx"}]
    with rt.session_factory() as db:
        metadata = db.get(EmailMetadata, mail_version)
        assert metadata is not None and metadata.subject == "Przegląd urządzeń – październik"
        notes = api.notes_of(db, mail_version)
    assert notes == [
        {"code": "skipped", "origin": [{"type": "attachment", "name": "logo.png"}],
         "reason": "unsupported"}
    ]  # fmt: skip

    status = c.get(f"/documents/{mail_doc}/status", headers={"HX-Request": "true"}).text
    assert "gotowe" in status and f"/ingestion/versions/{mail_version}/parts" in status
    fragment = c.get(f"/ingestion/versions/{mail_version}/parts").text
    for expected in ("załącznik: umowa.docx", "angielski", "polski", "Temat", "Przetwórz ponownie"):
        assert expected in fragment
    assert "logo.png: pominięto, nieobsługiwany typ pliku" in fragment

    # The outbox delivered thin events (identifiers only) after the commit.
    assert wait_for(lambda: any(e["version_id"] == str(mail_version) for e in EXTRACTED))
    event = next(e for e in EXTRACTED if e["version_id"] == str(mail_version))
    assert event == {"document_id": str(mail_doc), "version_id": str(mail_version)}
    with rt.session_factory() as db:
        entry = db.scalars(
            select(OutboxEntry).where(OutboxEntry.payload["version_id"].astext == str(mail_version))
        ).one()
        assert entry.delivered_at is not None


def test_long_txt_is_split_into_numbered_chunks(
    app: FastAPI, rt: runtime.Runtime, worker: dramatiq.Worker
) -> None:
    c = login(app, make_user(app, "publisher"))
    line = "Długi dokument testowy o przeglądach urządzeń chłodniczych w spółdzielni.\n"
    text = line * 3500  # about 260,000 characters
    _, version_id = upload(c, "dlugi.txt", text.encode())
    assert wait_final(rt, version_id).processing_status == "done"
    stored = parts(rt, version_id)
    assert [p.chunk_no for p in stored] == [1, 2, 3]
    assert [p.seq for p in stored] == [1, 2, 3]
    assert all(p.char_count <= 100_000 and p.language == "pl" for p in stored)
    assert "".join(p.text for p in stored).replace("\n", "") == text.replace("\n", "")


def test_processing_twice_does_not_duplicate_parts(
    app: FastAPI, rt: runtime.Runtime, worker: dramatiq.Worker
) -> None:
    owner = make_user(app, "publisher")
    c = login(app, owner)
    doc_id, version_id = upload(
        c, "a.eml", eml_bytes(attachments=[("b.txt", b"tekst", "text/plain")])
    )
    wait_final(rt, version_id)
    first = parts(rt, version_id)
    assert len(first) == 2
    assert pipeline.process(rt, version_id) == pipeline.Outcome.SKIPPED  # already done

    response = c.post(
        f"/ingestion/versions/{version_id}/reprocess",
        headers={"HX-Request": "true", "X-CSRF-Token": csrf(c, f"/documents/{doc_id}")},
        follow_redirects=False,
    )
    assert response.status_code == 204
    assert response.headers["hx-redirect"] == f"/documents/{doc_id}?done=reprocess"
    version = wait_final(rt, version_id)
    assert version.processing_status == "done" and version.processing_attempts == 1
    second = parts(rt, version_id)
    assert [(p.seq, p.text) for p in second] == [(p.seq, p.text) for p in first]
    with rt.session_factory() as db:
        assert len(db.scalars(select(Part).where(Part.version_id == version_id)).all()) == 2
        entry = db.scalars(
            select(AuditEntry).where(
                AuditEntry.action == "document.reprocess", AuditEntry.entity_id == str(doc_id)
            )
        ).one()
    assert entry.actor_user_id == owner.id
    assert set(entry.details) == {"version_id", "sha256"}


def test_a_permanent_error_fails_at_once_with_a_readable_message(
    app: FastAPI, rt: runtime.Runtime, worker: dramatiq.Worker
) -> None:
    c = login(app, make_user(app, "publisher"))
    doc_id, version_id = upload(c, "dos.txt", "Zażółć gęślą jaźń".encode("cp852"))
    version = wait_final(rt, version_id)
    assert version.processing_status == "failed" and version.processing_attempts == 1
    assert parts(rt, version_id) == []
    page = c.get(f"/documents/{doc_id}").text
    assert "Nie rozpoznano kodowania tekstu" in page and "błąd" in page
    time.sleep(1)
    assert version_of(rt, version_id).processing_attempts == 1  # no retry


# --- without a worker: retries, recovery, outbox ---------------------------------------------


@pytest.fixture
def uploaded(app: FastAPI, rt: runtime.Runtime, monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    """A pending TXT version; enqueueing is captured instead of sent."""
    SENT.clear()

    def capture(version_id: uuid.UUID | str, settings: Settings, *, delay_seconds: int = 0) -> None:
        SENT.append((str(version_id), delay_seconds))

    monkeypatch.setattr(tasks, "enqueue", capture)
    monkeypatch.setattr(startup, "enqueue", capture)
    c = login(app, make_user(app, "publisher"))
    _, version_id = upload(c, "a.txt", POLISH_TEXT.encode())
    SENT.clear()
    return version_id


def test_transient_errors_retry_with_backoff_then_give_up(
    rt: runtime.Runtime, uploaded: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object) -> None:
        raise TransientError("disk busy")

    monkeypatch.setattr(pipeline, "extract", broken)
    rt.settings = rt.settings.model_copy(update={"ingest_max_attempts": 2})
    assert pipeline.process(rt, uploaded) == pipeline.Outcome.RETRY
    version = version_of(rt, uploaded)
    assert (version.processing_status, version.processing_attempts) == ("pending", 1)
    assert version.processing_error == pipeline.RETRYING
    assert [(str(uploaded), 30)] == SENT

    assert pipeline.process(rt, uploaded) == pipeline.Outcome.FAILED
    version = version_of(rt, uploaded)
    assert (version.processing_status, version.processing_attempts) == ("failed", 2)
    assert version.processing_error == pipeline.GAVE_UP
    assert len(SENT) == 1


def test_retry_delay_grows_exponentially(settings: Settings) -> None:
    assert [pipeline.retry_delay(settings, n) for n in range(1, 7)] == [30, 60, 120, 240, 480, 600]


def test_unexpected_errors_fail_without_leaking_the_message(
    rt: runtime.Runtime,
    uploaded: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def crash(*_: object) -> None:
        raise ValueError("TREŚĆ-DOKUMENTU-W-WYJĄTKU")

    monkeypatch.setattr(pipeline, "extract", crash)
    assert pipeline.process(rt, uploaded) == pipeline.Outcome.FAILED
    assert version_of(rt, uploaded).processing_error == pipeline.INTERNAL
    assert "ValueError" in caplog.text and "TREŚĆ-DOKUMENTU" not in caplog.text


def test_logs_never_contain_text_names_or_headers(
    app: FastAPI,
    rt: runtime.Runtime,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(tasks, "enqueue", lambda *a, **k: None)
    c = login(app, make_user(app, "publisher"))
    mail = eml_bytes(
        subject="Temat-Sekretny", attachments=[("Zalacznik-Sekretny.txt", b"x", "text/plain")]
    )
    _, version_id = upload(c, "Nazwa-Sekretna.eml", mail)
    caplog.set_level("DEBUG")
    assert pipeline.process(rt, version_id) == pipeline.Outcome.DONE
    for secret in ("Sekretn", "Zażółć", "Umowa serwisowa", "anna@example.com"):
        assert secret not in caplog.text
    assert str(version_id) in caplog.text


def test_stuck_versions_are_recovered(rt: runtime.Runtime, uploaded: uuid.UUID) -> None:
    old = datetime.now(UTC) - timedelta(hours=2)
    with rt.session_factory.begin() as db:
        db.execute(
            update(Version)
            .where(Version.id == uploaded)
            .values(
                processing_status="processing", processing_started_at=old, processing_attempts=1
            )
        )
    result = startup.recover(rt)
    assert result.requeued_stuck >= 1
    assert version_of(rt, uploaded).processing_status == "pending"
    assert (str(uploaded), 0) in SENT

    with rt.session_factory.begin() as db:
        db.execute(
            update(Version)
            .where(Version.id == uploaded)
            .values(
                processing_status="processing", processing_started_at=old, processing_attempts=3
            )
        )
    startup.recover(rt)
    version = version_of(rt, uploaded)
    assert version.processing_status == "failed"
    assert version.processing_error == documents_processing.INTERRUPTED


def test_a_late_result_of_a_superseded_attempt_is_dropped(
    rt: runtime.Runtime, uploaded: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_extract = pipeline.extract

    def reprocess_meanwhile(*args: Any) -> Any:
        with rt.session_factory.begin() as db:  # someone presses "Process again" now
            db.execute(
                update(Version)
                .where(Version.id == uploaded)
                .values(processing_attempts=0, processing_status="pending")
            )
        return real_extract(*args)

    monkeypatch.setattr(pipeline, "extract", reprocess_meanwhile)
    assert pipeline.process(rt, uploaded) == pipeline.Outcome.SKIPPED
    assert parts(rt, uploaded) == []
    assert version_of(rt, uploaded).processing_status == "pending"


def test_outbox_skips_locked_rows_and_keeps_failed_deliveries(rt: runtime.Runtime) -> None:
    marker = uuid.uuid4().hex
    with rt.session_factory.begin() as db:
        for n in range(3):
            outbox.add(db, "test.event", {"marker": marker, "n": n})
    received: list[Mapping[str, Any]] = []
    bus = EventBus()

    def handler(payload: Mapping[str, Any]) -> None:
        if payload["n"] == 2:
            raise ZeroDivisionError
        received.append(payload)

    bus.subscribe("test.event", handler)

    with rt.session_factory.begin() as locker:  # another deliverer holds row n=0
        locker.scalars(
            select(OutboxEntry)
            .where(
                OutboxEntry.payload["marker"].astext == marker,
                OutboxEntry.payload["n"].astext == "0",
            )
            .with_for_update()
        ).one()
        outbox.deliver(rt.session_factory, bus)
        assert [p["n"] for p in received if p.get("marker") == marker] == [1]

    outbox.deliver(rt.session_factory, bus)
    assert sorted(p["n"] for p in received if p.get("marker") == marker) == [0, 1]
    with rt.session_factory() as db:
        failed = db.scalars(
            select(OutboxEntry).where(
                OutboxEntry.payload["marker"].astext == marker,
                OutboxEntry.payload["n"].astext == "2",
            )
        ).one()
        assert failed.delivered_at is None and failed.attempts == 2
        assert failed.last_error == "ZeroDivisionError"


def test_old_delivered_entries_are_pruned(rt: runtime.Runtime) -> None:
    marker = uuid.uuid4().hex
    with rt.session_factory.begin() as db:
        db.add(
            OutboxEntry(
                event="test.old",
                payload={"marker": marker},
                delivered_at=datetime.now(UTC) - timedelta(days=30),
            )
        )
        db.add(
            OutboxEntry(
                event="test.new", payload={"marker": marker}, delivered_at=datetime.now(UTC)
            )
        )
    assert outbox.prune(rt.session_factory, older_than_days=7) >= 1
    with rt.session_factory() as db:
        left = db.scalars(
            select(OutboxEntry.event).where(OutboxEntry.payload["marker"].astext == marker)
        ).all()
    assert left == ["test.new"]


def test_worker_start_cleans_up_and_requeues(rt: runtime.Runtime, uploaded: uuid.UUID) -> None:
    root = pipeline.workdir_root(rt.settings)
    orphan = root / "orphan-dir"
    orphan.mkdir()
    (orphan / "attachment-1").write_bytes(b"x")
    old = time.time() - 3 * 3600
    os.utime(orphan, (old, old))
    fresh = root / "in-use"
    fresh.mkdir(exist_ok=True)
    rt.redis.delete(startup.LOCK_KEY)

    startup.on_worker_start(rt)
    assert not orphan.exists() and fresh.exists()
    assert (str(uploaded), 0) in SENT
    SENT.clear()
    startup.on_worker_start(rt)  # another process of the same start: no second recovery
    assert SENT == []
    fresh.rmdir()


# --- "Process again": permissions, busy, CLI -------------------------------------------------


def test_reprocess_permissions(app: FastAPI, rt: runtime.Runtime, uploaded: uuid.UUID) -> None:
    with rt.session_factory() as db:
        version = db.get(Version, uploaded)
        assert version is not None
        doc_id = version.document_id

    def post(c: TestClient) -> int:
        token = csrf(c, "/")
        response = c.post(
            f"/ingestion/versions/{uploaded}/reprocess",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        return int(response.status_code)

    stranger = login(app, make_user(app, "publisher"))
    assert post(stranger) == 404
    assert stranger.get(f"/ingestion/versions/{uploaded}/parts").status_code == 404
    draft_reader = login(app, make_user(app, custom_codes={"documents.read_drafts"}))
    assert draft_reader.get(f"/ingestion/versions/{uploaded}/parts").status_code == 200
    assert (
        "Przetwórz ponownie" not in draft_reader.get(f"/ingestion/versions/{uploaded}/parts").text
    )
    assert post(draft_reader) == 403
    admin = login(app, make_user(app, "admin"))
    assert post(admin) == 303

    with rt.session_factory.begin() as db:
        db.execute(
            update(Version)
            .where(Version.id == uploaded)
            .values(processing_status="processing", processing_started_at=datetime.now(UTC))
        )
    response = admin.post(
        f"/ingestion/versions/{uploaded}/reprocess",
        data={"csrf_token": csrf(admin, "/")},
        follow_redirects=False,
    )
    assert response.headers["location"] == f"/documents/{doc_id}?done=busy"
    assert "jest właśnie przetwarzany" in admin.get(f"/documents/{doc_id}?done=busy").text


def test_cli_reprocess_and_redeliver(
    settings: Settings,
    rt: runtime.Runtime,
    uploaded: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("REDIS_URL", settings.redis_url)
    monkeypatch.setenv("STORAGE_DIR", settings.storage_dir)
    with rt.session_factory() as db:
        version = db.get(Version, uploaded)
        assert version is not None
        doc_id = version.document_id
    assert main(["reprocess", str(doc_id)]) == 0
    assert "Queued for processing again: 1" in capsys.readouterr().out
    with rt.session_factory() as db:
        entry = db.scalars(
            select(AuditEntry).where(
                AuditEntry.action == "document.reprocess", AuditEntry.entity_id == str(doc_id)
            )
        ).one()
        assert entry.actor_label == "cli" and entry.details["version_id"] == str(uploaded)
    assert main(["reprocess", "--stuck"]) == 0
    assert "Stuck versions" in capsys.readouterr().out
    assert main(["events", "redeliver"]) in (0, 1)
    assert "Events delivered" in capsys.readouterr().out
    assert main(["reprocess", str(uuid.uuid4())]) == 1


def test_parts_page_paths_are_checked(app: FastAPI) -> None:
    c = login(app, make_user(app, "admin"))
    assert c.get("/ingestion/versions/nie-uuid/parts").status_code == 404
    assert c.get(f"/ingestion/versions/{uuid.uuid4()}/parts").status_code == 404


@pytest.mark.parametrize(
    ("name", "data", "typed", "expected"),
    [
        (
            "Mail-Z-Biura.eml",
            eml_bytes(subject="Przegląd  ZIMOWY – faktura"),
            "",
            "Przegląd ZIMOWY – faktura",
        ),
        ("Mail-Z-Biura.eml", eml_bytes(subject="Temat"), "Mój Tytuł", "Mój Tytuł"),
        ("Mail-Z-Biura.eml", eml_bytes(subject="   "), "", "Mail-Z-Biura"),
        ("Notatka_ABC.txt", b"tekst", "", "Notatka_ABC"),
    ],
)
def test_default_title_of_e_mails_is_the_subject(
    app: FastAPI,
    rt: runtime.Runtime,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    data: bytes,
    typed: str,
    expected: str,
) -> None:
    monkeypatch.setattr(tasks, "enqueue", lambda *a, **k: None)
    c = login(app, make_user(app, "publisher"))
    response = c.post(
        "/documents/upload",
        files={"file": (name, data, "application/octet-stream")},
        data={"title": typed},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf(c, "/documents/upload")},
        follow_redirects=False,
    )
    doc_id = uuid.UUID(response.headers["hx-redirect"].split("/")[2].split("?")[0])
    with rt.session_factory() as db:
        document = db.get(Document, doc_id)
        assert document is not None and document.latest_version_id is not None
        version_id = document.latest_version_id
    assert pipeline.process(rt, version_id) == pipeline.Outcome.DONE
    with rt.session_factory() as db:
        document = db.get(Document, doc_id)
        assert document is not None and document.title == expected


def test_startup_hooks_run_once_the_schema_is_migrated(rt: runtime.Runtime) -> None:
    import threading

    orphan = pipeline.workdir_root(rt.settings) / "orphan-before-start"
    orphan.mkdir(exist_ok=True)
    old = time.time() - 3 * 3600
    os.utime(orphan, (old, old))
    thread = runtime.start_startup_hooks(rt, threading.Event())
    thread.join(timeout=30)
    assert not thread.is_alive() and not orphan.exists()


# --- stage C: PDF, DOC, MSG ------------------------------------------------------------------

needs_soffice = pytest.mark.skipif(
    shutil.which("soffice") is None, reason="needs LibreOffice (test image)"
)


def test_eml_with_pdf_and_msg_attachments_end_to_end(
    app: FastAPI, rt: runtime.Runtime, worker: dramatiq.Worker
) -> None:
    c = login(app, make_user(app, "publisher"))
    mail = eml_bytes(
        attachments=[
            ("raport.pdf", text_pdf([POLISH_TEXT, ENGLISH_TEXT]), "application/pdf"),
            (
                "notatka.msg",
                msg_bytes(Msg(body="Notatka z Outlooka: łódź i żółw.")),
                "application/vnd.ms-outlook",
            ),
        ]
    )
    _, version_id = upload(c, "z-zalacznikami.eml", mail)
    assert wait_final(rt, version_id).processing_status == "done"
    stored = parts(rt, version_id)
    origins = [(p.origin, p.page_no, p.language) for p in stored]
    pdf = [{"type": "attachment", "name": "raport.pdf"}]
    assert (pdf, 1, "pl") in origins and (pdf, 2, "en") in origins
    note = next(p for p in stored if p.origin == [{"type": "attachment", "name": "notatka.msg"}])
    assert note.text == "Notatka z Outlooka: łódź i żółw."
    fragment = c.get(f"/ingestion/versions/{version_id}/parts").text
    assert "załącznik: raport.pdf › strona 2" in fragment


def test_msg_upload_end_to_end(app: FastAPI, rt: runtime.Runtime, worker: dramatiq.Worker) -> None:
    c = login(app, make_user(app, "publisher"))
    doc_id, version_id = upload(c, "outlook.msg", msg_bytes(Msg(subject="Zebranie zarządu")))
    version = wait_final(rt, version_id)
    assert version.processing_status == "done"
    with rt.session_factory() as db:
        metadata = db.get(EmailMetadata, version_id)
        document = db.get(Document, doc_id)
    assert metadata is not None and metadata.subject == "Zebranie zarządu"
    assert metadata.to_addrs is not None and "jan@example.com" in metadata.to_addrs
    assert document is not None and document.title == "Zebranie zarządu"


@needs_soffice
def test_doc_upload_end_to_end(
    app: FastAPI, rt: runtime.Runtime, worker: dramatiq.Worker, tmp_path: Path
) -> None:
    doc = doc_bytes(
        tmp_path, ["Dokument Word 97: zażółć gęślą jaźń."], table=[["Pozycja", "Kwota"]]
    )
    c = login(app, make_user(app, "publisher"))
    _, version_id = upload(c, "stary.doc", doc)
    assert wait_final(rt, version_id, timeout=120).processing_status == "done"
    text = parts(rt, version_id)[0].text
    assert "Dokument Word 97: zażółć gęślą jaźń." in text and "Pozycja | Kwota" in text


def test_scanned_pdf_ends_as_needs_ocr(
    app: FastAPI, rt: runtime.Runtime, worker: dramatiq.Worker
) -> None:
    c = login(app, make_user(app, "publisher"))
    doc_id, version_id = upload(c, "skan.pdf", text_pdf([POLISH_TEXT, "x"], image_pages=(2,)))
    version = wait_final(rt, version_id)
    assert version.processing_status == "needs_ocr" and version.page_count == 2
    assert [(p.page_no, p.needs_ocr) for p in parts(rt, version_id)] == [(1, False), (2, True)]
    page = c.get(f"/documents/{doc_id}/status", headers={"HX-Request": "true"}).text
    assert "wymaga OCR" in page


def test_encrypted_pdf_fails_with_a_readable_message(
    app: FastAPI, rt: runtime.Runtime, worker: dramatiq.Worker
) -> None:
    c = login(app, make_user(app, "publisher"))
    doc_id, version_id = upload(c, "tajny.pdf", encrypted_pdf())
    version = wait_final(rt, version_id)
    assert version.processing_status == "failed" and version.processing_attempts == 1
    assert "zabezpieczony hasłem" in c.get(f"/documents/{doc_id}").text


def test_logs_of_new_formats_carry_no_content(
    app: FastAPI,
    rt: runtime.Runtime,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(tasks, "enqueue", lambda *a, **k: None)
    c = login(app, make_user(app, "publisher"))
    secret_msg = Msg(
        subject="Temat-Sekretny",
        sender="sekretny@example.com",
        body="Treść-Sekretna",
        attachments=[Attachment("Zalacznik-Sekretny.pdf", text_pdf(["Strona-Sekretna"]))],
    )
    uploads = [
        ("Nazwa-Sekretna.pdf", text_pdf(["Strona-Sekretna"])),
        ("Nazwa-Sekretna.msg", msg_bytes(secret_msg)),
        ("Uszkodzony-Sekretny.pdf", b"%PDF-1.4 Sekretny-Smiec"),
    ]
    caplog.set_level("DEBUG")
    for name, data in uploads:
        _, version_id = upload(c, name, data)
        pipeline.process(rt, version_id)
    for secret in ("Sekret", "sekretny@", "Strona-"):
        assert secret not in caplog.text


def test_child_timeout_is_retried_then_reported_as_too_long(
    rt: runtime.Runtime, uploaded: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    from baza_wiedzy.modules.ingestion.sandbox import IsolationTimeout

    def slow(*_: object) -> None:
        raise IsolationTimeout("timeout")

    monkeypatch.setattr(pipeline, "extract", slow)
    rt.settings = rt.settings.model_copy(update={"ingest_max_attempts": 2})
    assert pipeline.process(rt, uploaded) == pipeline.Outcome.RETRY
    assert version_of(rt, uploaded).processing_error == pipeline.RETRYING
    assert pipeline.process(rt, uploaded) == pipeline.Outcome.FAILED
    assert version_of(rt, uploaded).processing_error == pipeline.TIMEOUT
