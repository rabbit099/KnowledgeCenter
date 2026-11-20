"""Several files in one upload form (Phase 4 stage B, part 1, ADR-032): results per file,
duplicates held for a decision, limits, the form without JavaScript, permissions, CSRF, audit
and logs without file names. Synthetic files only."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.models import AuditEntry
from baza_wiedzy.core.events import DOCUMENT_VERSION_UPLOADED
from baza_wiedzy.main import create_app
from baza_wiedzy.modules.documents import routes, upload
from baza_wiedzy.modules.documents.models import Document, Version
from fixtures.msg import Msg, msg_bytes
from fixtures.samples import docx_bytes, eml_bytes, text_pdf
from web_helpers import csrf, login, make_user

pytestmark = pytest.mark.integration

KB = 1024


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    app = create_app(
        settings.model_copy(
            update={
                "db_pool_size": 3,
                "db_max_overflow": 0,
                "max_upload_mb": 1,
                "upload_max_files": 6,
                "upload_max_total_mb": 2,
                "max_request_mb": 3,
            }
        ),
        setup_logging=False,
    )
    app.state.uploaded_events = []
    app.state.events.subscribe(DOCUMENT_VERSION_UPLOADED, app.state.uploaded_events.append)
    return app


def unique(text: str = "Notatka") -> bytes:
    return f"{text} {uuid.uuid4()}: zażółć gęślą jaźń.\n".encode()


def send_many(
    c: TestClient, files: list[tuple[str, bytes]], *, htmx: bool = True
) -> httpx.Response:
    headers = {"HX-Request": "true", "X-CSRF-Token": csrf(c, "/documents/upload")} if htmx else {}
    data = {} if htmx else {"csrf_token": csrf(c, "/documents/upload")}
    response: httpx.Response = c.post(
        "/documents/upload",
        files=[("files", (name, body, "application/octet-stream")) for name, body in files],
        data=data,
        headers=headers,
        follow_redirects=False,
    )
    return response


def batch_url(response: httpx.Response) -> str:
    location = response.headers.get("hx-redirect") or response.headers["location"]
    assert location.startswith("/documents?batch="), location
    return location


def documents_of(app: FastAPI, owner_id: uuid.UUID) -> list[tuple[str, str]]:
    with app.state.session_factory() as db:
        rows = db.execute(
            select(Version.original_filename, Version.kind)
            .join(Document, Document.latest_version_id == Version.id)
            .where(Document.owner_id == owner_id)
            .order_by(Version.original_filename)
        )
        return [(name, kind) for name, kind in rows]


def uploads_audited(app: FastAPI, owner_id: uuid.UUID) -> int:
    with app.state.session_factory() as db:
        count = db.scalar(
            select(func.count())
            .select_from(AuditEntry)
            .where(AuditEntry.action == "document.upload", AuditEntry.actor_user_id == owner_id)
        )
        return int(count or 0)


def test_files_of_different_types_in_one_form(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    owner = make_user(app, "publisher")
    c = login(app, owner)
    files = [
        ("notatka-wielu.txt", unique()),
        ("raport-wielu.pdf", text_pdf([f"Raport {uuid.uuid4()}"])),
        ("umowa-wielu.docx", docx_bytes([f"Umowa {uuid.uuid4()}"])),
        ("mail-wielu.eml", eml_bytes(subject=f"Temat {uuid.uuid4()}", plain="Treść.")),
        ("outlook-wielu.msg", msg_bytes(Msg(subject=f"Outlook {uuid.uuid4()}"))),
    ]
    response = send_many(c, files)
    assert response.status_code == 204
    page = c.get(batch_url(response)).text
    assert "Wgrano: 5" in page and "odrzucono" not in page
    assert page.count('<span class="badge badge-ok">wgrano</span>') == 5
    assert documents_of(app, owner.id) == [
        ("mail-wielu.eml", "eml"),
        ("notatka-wielu.txt", "txt"),
        ("outlook-wielu.msg", "msg"),
        ("raport-wielu.pdf", "pdf"),
        ("umowa-wielu.docx", "docx"),
    ]
    assert uploads_audited(app, owner.id) == 5  # one audit entry per file
    assert len(app.state.uploaded_events) == 5  # each file queued for processing on its own
    assert "wielu" not in caplog.text  # no file names in the application log
    # The title field applies to one file only: several files keep their file names.
    incoming = Path(app.state.settings.storage_dir) / ".incoming"
    assert not any(incoming.iterdir())


def test_one_bad_file_never_stops_the_others(app: FastAPI) -> None:
    owner = make_user(app, "publisher")
    c = login(app, owner)
    response = send_many(
        c,
        [
            ("dobry-1.txt", unique()),
            ("za-duzy.txt", b"a" * (1024 * KB + 1)),
            ("program.exe", b"MZ\x90\x00"),
            ("pusty.txt", b""),
            ("dobry-2.txt", unique()),
        ],
    )
    page = c.get(batch_url(response)).text
    assert "Wgrano: 2" in page and "odrzucono: 3" in page
    assert "większy niż dozwolony" in page and "nie jest obsługiwany" in page
    assert "Plik jest pusty" in page
    assert [name for name, _ in documents_of(app, owner.id)] == ["dobry-1.txt", "dobry-2.txt"]
    assert uploads_audited(app, owner.id) == 2


def test_an_unexpected_error_in_one_file_is_isolated(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real = upload.create

    def flaky(*args: Any, **kwargs: Any) -> upload.Uploaded:
        if kwargs["raw_filename"] == "awaria-tajna.txt":
            raise RuntimeError("awaria-tajna.txt")  # message must never reach the log
        return real(*args, **kwargs)

    monkeypatch.setattr(upload, "create", flaky)
    caplog.set_level(logging.DEBUG)
    owner = make_user(app, "publisher")
    c = login(app, owner)
    response = send_many(c, [("awaria-tajna.txt", unique()), ("dalej.txt", unique())])
    page = c.get(batch_url(response)).text
    assert "Wgrano: 1" in page and "Nie udało się zapisać pliku" in page
    assert [name for name, _ in documents_of(app, owner.id)] == ["dalej.txt"]
    assert "upload of one file failed: RuntimeError" in caplog.text
    assert "awaria-tajna" not in caplog.text
    incoming = Path(app.state.settings.storage_dir) / ".incoming"
    assert not any(incoming.iterdir())  # the failed file's copy is removed at once


def test_more_files_than_the_limit_are_refused_per_file(app: FastAPI) -> None:
    owner = make_user(app, "publisher")
    c = login(app, owner)
    response = send_many(c, [(f"plik-{n}.txt", unique()) for n in range(8)])  # limit 6
    page = c.get(batch_url(response)).text
    assert "Wgrano: 6" in page and "odrzucono: 2" in page
    assert page.count("Więcej plików, niż przyjmuje jedno wgranie (6)") == 2
    assert len(documents_of(app, owner.id)) == 6


def test_far_too_many_files_or_bytes_refuse_the_whole_form_readably(app: FastAPI) -> None:
    c = login(app, make_user(app, "publisher"))
    response = send_many(c, [(f"p-{n}.txt", b"x") for n in range(6 + routes.EXTRA_FILES + 1)])
    assert response.status_code == 413
    assert "Za dużo plików w jednym wgraniu: najwyżej 6" in response.text
    big = [("a.txt", b"a" * (900 * KB)), ("b.txt", b"b" * (900 * KB)), ("c.txt", b"c" * 400 * KB)]
    response = send_many(c, big)  # 2.2 MB > UPLOAD_MAX_TOTAL_MB 2
    assert response.status_code == 413
    assert "Pliki są razem większe niż dozwolony rozmiar (2 MB)" in response.text


def test_duplicates_wait_for_a_decision_per_file(app: FastAPI) -> None:
    owner = make_user(app, "publisher")
    c = login(app, owner)
    known_a, known_b = unique("A"), unique("B")
    send_many(c, [("oryginal-a.txt", known_a), ("oryginal-b.txt", known_b)])
    response = send_many(
        c, [("kopia-a.txt", known_a), ("kopia-b.txt", known_b), ("nowy.txt", unique())]
    )
    url = batch_url(response)
    page = c.get(url).text
    assert "Wgrano: 1" in page and "czeka na Twoją decyzję (możliwe duplikaty): 2" in page
    assert page.count("Wgraj mimo to") == 2
    batch_id = url.split("batch=")[1]
    token = csrf(c, url)
    keep = c.post(
        f"/documents/upload/batches/{batch_id}/0",
        data={"csrf_token": token, "decision": "keep"},
        follow_redirects=False,
    )
    assert keep.status_code == 303 and keep.headers["location"] == url
    skip = c.post(
        f"/documents/upload/batches/{batch_id}/1",
        data={"csrf_token": token, "decision": "skip"},
        follow_redirects=False,
    )
    assert skip.status_code == 303
    page = c.get(url).text
    assert "Wgrano: 2" in page and "pominięto: 1" in page and "Wgraj mimo to" not in page
    names = [name for name, _ in documents_of(app, owner.id)]
    assert "kopia-a.txt" in names and "kopia-b.txt" not in names
    assert uploads_audited(app, owner.id) == 4  # 2 originals, nowy.txt, kopia-a.txt
    # A second decision on the same row (another tab) changes nothing.
    again = c.post(
        f"/documents/upload/batches/{batch_id}/0",
        data={"csrf_token": token, "decision": "keep"},
        follow_redirects=False,
    )
    assert again.status_code == 303 and uploads_audited(app, owner.id) == 4
    held = Path(app.state.settings.storage_dir) / ".batches" / batch_id
    assert not held.exists() or not any(held.iterdir())


def test_a_batch_belongs_to_its_uploader(app: FastAPI) -> None:
    c = login(app, make_user(app, "publisher"))
    content = unique()
    send_many(c, [("raz.txt", content)])
    url = batch_url(send_many(c, [("dwa.txt", content), ("trzy.txt", unique())]))
    batch_id = url.split("batch=")[1]
    stranger = login(app, make_user(app, "publisher"))
    page = stranger.get(url).text
    assert "Wynik wgrywania" not in page and "dwa.txt" not in page
    decision = stranger.post(
        f"/documents/upload/batches/{batch_id}/0",
        data={"csrf_token": csrf(stranger, "/documents"), "decision": "keep"},
        follow_redirects=False,
    )
    assert decision.status_code == 404
    bogus = stranger.post(
        "/documents/upload/batches/..%2F..%2Fetc/0",
        data={"csrf_token": csrf(stranger, "/documents"), "decision": "keep"},
        follow_redirects=False,
    )
    assert bogus.status_code == 404


def test_without_javascript_the_token_is_the_first_form_field(app: FastAPI) -> None:
    owner = make_user(app, "publisher")
    c = login(app, owner)
    response = send_many(c, [("bez-js-1.txt", unique()), ("bez-js-2.txt", unique())], htmx=False)
    assert response.status_code == 303 and "Wgrano: 2" in c.get(batch_url(response)).text

    token = csrf(c, "/documents/upload")
    boundary = "granica" + uuid.uuid4().hex

    def body(parts: list[tuple[str, str | None, bytes]]) -> bytes:
        out = b""
        for name, filename, value in parts:
            disposition = f'form-data; name="{name}"'
            if filename:
                disposition += f'; filename="{filename}"'
            out += f"--{boundary}\r\nContent-Disposition: {disposition}\r\n\r\n".encode()
            out += value + b"\r\n"
        return out + f"--{boundary}--\r\n".encode()

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    late = body([("files", "pozno.txt", unique()), ("csrf_token", None, token.encode())])
    refused = c.post("/documents/upload", content=late, headers=headers)
    assert refused.status_code == 403  # token after the files: not read, refused
    missing = body([("files", "brak.txt", unique())])
    assert c.post("/documents/upload", content=missing, headers=headers).status_code == 403
    wrong = body([("csrf_token", None, b"0" * 64), ("files", "zly.txt", unique())])
    assert c.post("/documents/upload", content=wrong, headers=headers).status_code == 403
    assert "pozno.txt" not in [n for n, _ in documents_of(app, owner.id)]


def test_only_uploaders_upload_and_decide(app: FastAPI) -> None:
    reader = login(app, make_user(app, "reader"))
    response = reader.post(
        "/documents/upload",
        files=[("files", ("a.txt", unique(), "text/plain"))],
        headers={"X-CSRF-Token": csrf(reader, "/")},
        follow_redirects=False,
    )
    assert response.status_code == 403
    decision = reader.post(
        f"/documents/upload/batches/{uuid.uuid4().hex}/0",
        data={"csrf_token": csrf(reader, "/"), "decision": "keep"},
        follow_redirects=False,
    )
    assert decision.status_code == 403
    assert reader.get("/documents/upload").status_code == 403


def test_single_file_keeps_the_document_page_and_the_form_error(app: FastAPI) -> None:
    c = login(app, make_user(app, "publisher"))
    one = send_many(c, [("jeden.txt", unique())])
    assert one.status_code == 204 and "?done=uploaded" in one.headers["hx-redirect"]
    refused = send_many(c, [("jeden.exe", b"MZ\x90\x00")])
    assert refused.status_code == 422 and "nie jest obsługiwany" in refused.text
