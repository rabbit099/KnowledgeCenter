"""Upload into the documents module: storage, validation, visibility, duplicates, audit."""

from __future__ import annotations

import hashlib
import stat
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.models import AuditEntry
from baza_wiedzy.core.events import DOCUMENT_VERSION_UPLOADED
from baza_wiedzy.main import create_app
from baza_wiedzy.modules.documents.models import Document, Version
from fixtures.ole2 import build
from web_helpers import csrf, login, make_user

pytestmark = pytest.mark.integration

TEXT = "Umowa serwisowa: zażółć gęślą jaźń.\n".encode()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    app = create_app(
        settings.model_copy(update={"db_pool_size": 3, "db_max_overflow": 0, "max_upload_mb": 1}),
        setup_logging=False,
    )
    app.state.uploaded_events = []
    app.state.events.subscribe(DOCUMENT_VERSION_UPLOADED, app.state.uploaded_events.append)
    return app


def send(
    c: TestClient, name: str, data: bytes, *, title: str = "", header: bool = True
) -> httpx.Response:
    headers = {"HX-Request": "true"}
    if header:
        headers["X-CSRF-Token"] = csrf(c, "/documents/upload")
    response: httpx.Response = c.post(
        "/documents/upload",
        files={"file": (name, data, "application/octet-stream")},
        data={"title": title},
        headers=headers,
        follow_redirects=False,
    )
    return response


def document_id(response: httpx.Response) -> uuid.UUID:
    assert response.status_code == 204, response.text
    location = response.headers["hx-redirect"]
    return uuid.UUID(location.split("/")[2].split("?")[0])


def test_upload_stores_a_draft_and_audits_without_the_file_name(app: FastAPI) -> None:
    owner = make_user(app, "publisher")
    c = login(app, owner)
    response = send(c, "../../Umowa serwisowa‮.txt", TEXT, title="  Umowa   2026 ")
    doc_id = document_id(response)
    with app.state.session_factory() as db:
        document = db.get(Document, doc_id)
        assert document is not None
        version = db.get(Version, document.latest_version_id)
        assert (document.status, document.title, document.owner_id) == (
            "draft",
            "Umowa 2026",
            owner.id,
        )
        assert version is not None
        assert (version.kind, version.processing_status, version.size_bytes) == (
            "txt",
            "pending",
            len(TEXT),
        )
        assert version.original_filename == "Umowa serwisowa.txt"
        assert version.sha256 == hashlib.sha256(TEXT).hexdigest()
        stored = (
            Path(app.state.settings.storage_dir)
            / "originals"
            / version.storage_key[:2]
            / version.storage_key[2:4]
            / version.storage_key
        )
        assert stored.read_bytes() == TEXT
        assert not stat.S_IMODE(stored.stat().st_mode) & 0o222
        entry = db.scalars(
            select(AuditEntry).where(
                AuditEntry.action == "document.upload", AuditEntry.entity_id == str(doc_id)
            )
        ).one()
        assert set(entry.details) == {"version_id", "sha256", "size_bytes", "kind"}
        assert "Umowa" not in str(entry.details)
    assert app.state.uploaded_events[-1] == {
        "document_id": str(doc_id),
        "version_id": str(version.id),
    }
    incoming = Path(app.state.settings.storage_dir) / ".incoming"
    assert not any(incoming.iterdir())
    page = c.get(f"/documents/{doc_id}?done=uploaded").text
    assert "Plik został wgrany" in page and "oczekuje" in page
    assert "Umowa 2026" in c.get("/documents").text


def test_drafts_are_visible_only_to_the_owner_and_draft_readers(app: FastAPI) -> None:
    c = login(app, make_user(app, "publisher"))
    doc_id = document_id(send(c, "tajny.txt", TEXT))
    for other in (make_user(app, "publisher"), make_user(app, "reader")):
        other_client = login(app, other)
        assert other_client.get(f"/documents/{doc_id}").status_code == 404
        assert other_client.get(f"/documents/{doc_id}/status").status_code == 404
        assert "tajny" not in other_client.get("/documents?scope=all").text
    admin = login(app, make_user(app, "admin"))
    assert admin.get(f"/documents/{doc_id}").status_code == 200
    assert "tajny" in admin.get("/documents?scope=all").text
    assert "tajny" not in admin.get("/documents").text  # "my documents" stays the default


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("raport.pdf", TEXT, "nie pasuje do jego rozszerzenia"),
        ("arkusz.doc", build({"Workbook": b"\0" * 600}), "nie pasuje do jego rozszerzenia"),
        ("program.exe", b"MZ\x90\x00", "nie jest obsługiwany"),
        ("pusty.txt", b"", "Plik jest pusty"),
    ],
)
def test_refused_files_leave_nothing_behind(
    app: FastAPI, name: str, data: bytes, message: str
) -> None:
    c = login(app, make_user(app, "publisher"))
    response = send(c, name, data)
    assert response.status_code == 422 and message in response.text
    incoming = Path(app.state.settings.storage_dir) / ".incoming"
    assert not incoming.exists() or not any(incoming.iterdir())


def test_size_limit(app: FastAPI) -> None:
    c = login(app, make_user(app, "publisher"))
    response = send(c, "duzy.txt", b"a" * (1024 * 1024 + 1))
    assert response.status_code == 413 and "większy niż dozwolony" in response.text
    assert send(c, "w-limicie.txt", b"a" * (1024 * 1024)).status_code == 204


def test_upload_needs_the_csrf_header_and_the_permission(app: FastAPI) -> None:
    publisher = login(app, make_user(app, "publisher"))
    assert send(publisher, "a.txt", TEXT, header=False).status_code == 403
    reader = login(app, make_user(app, "reader"))
    assert send(reader, "a.txt", TEXT).status_code == 403
    page = publisher.get("/documents/upload").text
    assert "<noscript>" not in page  # works without JavaScript (ADR-032)
    assert 'enctype="multipart/form-data"' in page and 'name="files" multiple' in page


def test_duplicate_is_held_only_for_documents_the_user_can_see(app: FastAPI) -> None:
    content = f"Unikalna treść {uuid.uuid4()}".encode()
    first_owner = login(app, make_user(app, "publisher"))
    first = document_id(send(first_owner, "pierwszy.txt", content, title="Pierwszy"))
    held = send(first_owner, "drugi.txt", content)
    assert held.status_code == 204 and "/documents?batch=" in held.headers["hx-redirect"]
    page = first_owner.get(held.headers["hx-redirect"]).text
    assert "możliwy duplikat" in page and f"/documents/{first}" in page
    assert "Wgraj mimo to" in page and "Pomiń" in page

    stranger = login(app, make_user(app, "publisher"))
    third = document_id(send(stranger, "trzeci.txt", content))  # the other person's draft:
    page = stranger.get(f"/documents/{third}").text  # invisible, so no duplicate
    assert "Możliwy duplikat" not in page
    assert "Pierwszy" not in page


def test_the_status_fragment_polls_while_waiting(app: FastAPI) -> None:
    c = login(app, make_user(app, "publisher"))
    doc_id = document_id(send(c, "a.txt", TEXT))
    fragment = c.get(f"/documents/{doc_id}/status", headers={"HX-Request": "true"}).text
    assert 'hx-trigger="every 3s"' in fragment and "<html" not in fragment
