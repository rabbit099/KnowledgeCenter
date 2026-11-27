"""Document lifecycle (publish, unpublish, archive, restore), visibility and download (ADR-025).

The role matrix covers every action from every source status for: the owner (Publisher), another
Publisher, a Reader, Admin, custom roles with publish_any or edit_any (each with read_drafts, as
the role editor requires) and an owner who may upload but not publish.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from baza_wiedzy.core.admin.roles import missing_requirements
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.models import AuditEntry, Role, RolePermission
from baza_wiedzy.core.events import DOCUMENT_PUBLISHED
from baza_wiedzy.core.logs import JsonFormatter
from baza_wiedzy.main import create_app
from baza_wiedzy.modules.documents.models import Document
from baza_wiedzy.modules.ingestion import tasks
from web_helpers import csrf, login, make_user

pytestmark = pytest.mark.integration

CONTENT = "Umowa serwisowa nr UM/2026/017 zawarta w Łodzi.\n".encode()
FILENAME = "Umowa „serwisowa” – Łódź.txt"


@pytest.fixture(scope="module")
def app(migrated: Settings) -> Iterator[FastAPI]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tasks, "enqueue", lambda *a, **k: None)  # no broker in this test
        app = create_app(
            migrated.model_copy(update={"db_pool_size": 3, "db_max_overflow": 0}),
            setup_logging=False,
        )
        app.state.published_events = []
        app.state.events.subscribe(DOCUMENT_PUBLISHED, app.state.published_events.append)
        yield app


CUSTOM = {
    "publish_any": {"documents.read", "documents.read_drafts", "documents.publish_any"},
    "edit_any": {"documents.read", "documents.read_drafts", "documents.edit_any"},
    "uploader": {"documents.read", "documents.upload"},
}


@pytest.fixture(scope="module")
def clients(app: FastAPI) -> dict[str, TestClient]:
    users = {
        "owner": make_user(app, "publisher"),
        "other_publisher": make_user(app, "publisher"),
        "reader": make_user(app, "reader"),
        "admin": make_user(app, "admin"),
        **{name: make_user(app, custom_codes=codes) for name, codes in CUSTOM.items()},
    }
    app.state.users = users
    return {name: login(app, user) for name, user in users.items()}


def upload(c: TestClient, name: str = FILENAME, data: bytes | None = None) -> uuid.UUID:
    # Unique content by default: identical content would be held as a possible duplicate.
    data = data if data is not None else CONTENT + f"{uuid.uuid4()}\n".encode()
    response: httpx.Response = c.post(
        "/documents/upload",
        files={"file": (name, data, "application/octet-stream")},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf(c, "/documents/upload")},
        follow_redirects=False,
    )
    assert response.status_code == 204, response.text
    return uuid.UUID(response.headers["hx-redirect"].split("/")[2].split("?")[0])


def set_status(app: FastAPI, doc_id: uuid.UUID, status: str) -> None:
    with app.state.session_factory.begin() as db:
        document = db.get(Document, doc_id)
        assert document is not None
        document.published_version_id = document.latest_version_id if status != "draft" else None
        document.status = status


def act(
    c: TestClient, doc_id: uuid.UUID, action: str, expected: str, *, token_page: str = "/"
) -> httpx.Response:
    response: httpx.Response = c.post(
        f"/documents/{doc_id}/lifecycle",
        data={"action": action, "expected": expected, "csrf_token": csrf(c, token_page)},
        follow_redirects=False,
    )
    return response


def status_of(app: FastAPI, doc_id: uuid.UUID) -> str:
    with app.state.session_factory() as db:
        value = db.scalar(select(Document.status).where(Document.id == doc_id))
    assert value is not None
    return str(value)


# (actor, owner of the document, status before, action, expected HTTP status)
MATRIX = [
    # publish: own with documents.publish, others' with publish_any (+ read_drafts to see them)
    ("owner", "owner", "draft", "publish", 303),
    ("other_publisher", "owner", "draft", "publish", 404),
    ("reader", "owner", "draft", "publish", 404),
    ("admin", "owner", "draft", "publish", 303),
    ("publish_any", "owner", "draft", "publish", 303),
    ("edit_any", "owner", "draft", "publish", 403),
    ("uploader", "uploader", "draft", "publish", 403),
    # unpublish: the same rule
    ("owner", "owner", "published", "unpublish", 303),
    ("other_publisher", "owner", "published", "unpublish", 403),
    ("reader", "owner", "published", "unpublish", 403),
    ("admin", "owner", "published", "unpublish", 303),
    ("publish_any", "owner", "published", "unpublish", 303),
    ("edit_any", "owner", "published", "unpublish", 403),
    # archive: own with documents.publish, others' with edit_any
    ("owner", "owner", "published", "archive", 303),
    ("owner", "owner", "draft", "archive", 303),
    ("other_publisher", "owner", "published", "archive", 403),
    ("other_publisher", "owner", "draft", "archive", 404),
    ("reader", "owner", "published", "archive", 403),
    ("admin", "owner", "published", "archive", 303),
    ("publish_any", "owner", "published", "archive", 403),
    ("edit_any", "owner", "published", "archive", 303),
    ("edit_any", "owner", "draft", "archive", 303),
    ("uploader", "uploader", "draft", "archive", 403),
    # restore (to draft): own with documents.publish, others' with edit_any
    ("owner", "owner", "archived", "restore", 303),
    ("other_publisher", "owner", "archived", "restore", 404),
    ("reader", "owner", "archived", "restore", 404),
    ("admin", "owner", "archived", "restore", 303),
    ("publish_any", "owner", "archived", "restore", 404),
    ("edit_any", "owner", "archived", "restore", 303),
    ("uploader", "uploader", "archived", "restore", 403),
]
TARGET = {"publish": "published", "unpublish": "draft", "archive": "archived", "restore": "draft"}


@pytest.mark.parametrize(("actor", "owner", "before", "action", "expected"), MATRIX)
def test_role_matrix(
    app: FastAPI,
    clients: dict[str, TestClient],
    actor: str,
    owner: str,
    before: str,
    action: str,
    expected: int,
) -> None:
    doc_id = upload(clients[owner])
    set_status(app, doc_id, before)
    response = act(clients[actor], doc_id, action, before)
    assert response.status_code == expected, response.text[:300]
    after = status_of(app, doc_id)
    if expected == 303:
        assert response.headers["location"] == f"/documents/{doc_id}?done={action}"
        assert after == TARGET[action]
    else:
        assert after == before
    if expected == 403:
        assert "Nie masz uprawnień do tej akcji" in response.text


def test_transitions_are_audited_with_identifiers_only(
    app: FastAPI, clients: dict[str, TestClient]
) -> None:
    owner = clients["owner"]
    doc_id = upload(owner)
    for action, before in (
        ("publish", "draft"),
        ("unpublish", "published"),
        ("publish", "draft"),
        ("archive", "published"),
        ("restore", "archived"),
    ):
        assert act(owner, doc_id, action, before).status_code == 303
    with app.state.session_factory() as db:
        entries = list(
            db.scalars(
                select(AuditEntry)
                .where(AuditEntry.entity_id == str(doc_id), AuditEntry.action != "document.upload")
                .order_by(AuditEntry.id)
            )
        )
        document = db.get(Document, doc_id)
    assert document is not None
    assert [e.action for e in entries] == [
        "document.publish",
        "document.unpublish",
        "document.publish",
        "document.archive",
        "document.restore",
    ]
    for entry in entries:
        assert set(entry.details) == {"version_id", "from", "to"}
        assert entry.details["version_id"] == str(document.latest_version_id)
        assert "Umowa" not in str(entry.details) and "Łódź" not in str(entry.details)
    assert entries[3].details == {
        "version_id": str(document.latest_version_id),
        "from": "published",
        "to": "archived",
    }
    assert (document.status, document.published_version_id, document.archived_at) == (
        "draft",
        None,
        None,
    )


def test_publication_sets_the_version_and_publishes_the_event(
    app: FastAPI, clients: dict[str, TestClient]
) -> None:
    doc_id = upload(clients["owner"])
    before = datetime.now(UTC)
    assert act(clients["owner"], doc_id, "publish", "draft").status_code == 303
    with app.state.session_factory() as db:
        document = db.get(Document, doc_id)
    assert document is not None
    assert document.published_version_id == document.latest_version_id
    assert document.published_by == app.state.users["owner"].id
    assert document.published_at is not None and document.published_at >= before
    assert app.state.published_events[-1] == {
        "document_id": str(doc_id),
        "version_id": str(document.latest_version_id),
    }
    assert act(clients["owner"], doc_id, "archive", "published").status_code == 303
    with app.state.session_factory() as db:
        archived = db.get(Document, doc_id)
    assert archived is not None
    # The published version is remembered while archived (search shows it with the filter).
    assert archived.published_version_id == document.latest_version_id
    assert archived.archived_at is not None


def test_stale_page_and_unknown_action_are_refused(
    app: FastAPI, clients: dict[str, TestClient]
) -> None:
    owner = clients["owner"]
    doc_id = upload(owner)
    set_status(app, doc_id, "published")
    response = act(owner, doc_id, "publish", "draft")  # page rendered before someone published
    assert response.status_code == 409
    assert "Stan dokumentu zmienił się w międzyczasie" in response.text
    response = act(owner, doc_id, "archive", "draft")
    assert response.status_code == 409
    assert status_of(app, doc_id) == "published"
    assert act(owner, doc_id, "delete", "published").status_code == 400
    assert act(owner, uuid.uuid4(), "publish", "draft").status_code == 404
    response = owner.post(
        "/documents/not-a-uuid/lifecycle",
        data={"action": "publish", "expected": "draft", "csrf_token": csrf(owner, "/")},
    )
    assert response.status_code == 404


def test_a_published_document_must_name_its_version(
    app: FastAPI, clients: dict[str, TestClient]
) -> None:
    doc_id = upload(clients["owner"])
    with (
        pytest.raises(Exception, match="ck_doc_documents_published_version"),
        app.state.session_factory.begin() as db,
    ):
        db.execute(
            text("UPDATE doc_documents SET status = 'published' WHERE id = :id"), {"id": doc_id}
        )


def test_visibility_owner_sees_every_state_readers_never_archived(
    app: FastAPI, clients: dict[str, TestClient]
) -> None:
    doc_id = upload(clients["owner"], name="widocznosc-test.txt")
    page = f"/documents/{doc_id}"
    expectations = {
        # status: (owner, reader, other publisher, admin)
        "draft": (200, 404, 404, 200),
        "published": (200, 200, 200, 200),
        "archived": (200, 404, 404, 200),
    }
    for status, codes in expectations.items():
        set_status(app, doc_id, status)
        got = tuple(
            clients[who].get(page).status_code
            for who in ("owner", "reader", "other_publisher", "admin")
        )
        assert got == codes, status
    set_status(app, doc_id, "archived")
    listing = clients["owner"].get("/documents").text
    assert "widocznosc-test" in listing and "zarchiwizowany" in listing
    detail = clients["owner"].get(page).text
    assert ">Przywróć<" in detail and ">Publikuj<" not in detail


def test_document_page_shows_only_allowed_actions_and_the_search_warning(
    app: FastAPI, clients: dict[str, TestClient]
) -> None:
    doc_id = upload(clients["owner"])
    page = f"/documents/{doc_id}"
    owner_page = clients["owner"].get(page).text
    assert ">Publikuj<" in owner_page and ">Archiwizuj<" in owner_page
    assert "Cofnij publikację" not in owner_page
    # Not processed yet (no worker here): publication is allowed, with a warning.
    assert "po publikacji treści nie będzie można wyszukać" in owner_page
    assert "Pobierz oryginał" in owner_page
    set_status(app, doc_id, "published")
    reader_page = clients["reader"].get(page).text
    assert "Pobierz oryginał" in reader_page
    assert "lifecycle" not in reader_page  # no action forms for a Reader
    assert "Treści tego dokumentu nie można wyszukać" in reader_page
    assert "Cofnij publikację" in clients["owner"].get(page).text


def test_download_headers_name_and_audit(app: FastAPI, clients: dict[str, TestClient]) -> None:
    content = CONTENT + f"{uuid.uuid4()}\n".encode()
    doc_id = upload(clients["owner"], data=content)
    set_status(app, doc_id, "published")
    response = clients["reader"].get(f"/documents/{doc_id}/download")
    assert response.status_code == 200
    assert response.content == content
    headers = response.headers
    assert headers["content-type"] == "application/octet-stream"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["cache-control"] == "private, no-store"
    assert headers["content-length"] == str(len(content))
    assert "accept-ranges" not in headers
    assert headers.get_list("content-security-policy") == ["default-src 'none'; sandbox"]
    assert len(headers.get_list("cache-control")) == 1
    disposition = headers["content-disposition"]
    assert disposition.startswith('attachment; filename="Umowa serwisowa Lodz.txt"; ')
    assert unquote(disposition.split("filename*=UTF-8''")[1]) == FILENAME
    with app.state.session_factory() as db:
        entry = db.scalars(
            select(AuditEntry)
            .where(AuditEntry.action == "document.download", AuditEntry.entity_id == str(doc_id))
            .order_by(AuditEntry.id.desc())
        ).first()
        document = db.get(Document, doc_id)
    assert entry is not None and document is not None
    assert entry.actor_user_id == app.state.users["reader"].id
    assert entry.details == {"version_id": str(document.published_version_id)}


def test_download_follows_visibility(app: FastAPI, clients: dict[str, TestClient]) -> None:
    doc_id = upload(clients["owner"])
    url = f"/documents/{doc_id}/download"
    assert clients["reader"].get(url).status_code == 404
    assert clients["other_publisher"].get(url).status_code == 404
    assert clients["owner"].get(url).status_code == 200
    assert clients["admin"].get(url).status_code == 200
    set_status(app, doc_id, "archived")
    assert clients["reader"].get(url).status_code == 404
    assert clients["owner"].get(url).status_code == 200
    assert clients["reader"].get(f"/documents/{uuid.uuid4()}/download").status_code == 404
    with app.state.session_factory() as db:
        denied = db.scalar(
            select(AuditEntry).where(
                AuditEntry.action == "document.download",
                AuditEntry.entity_id == str(doc_id),
                AuditEntry.actor_user_id == app.state.users["reader"].id,
            )
        )
    assert denied is None  # refused downloads are not audited as downloads


# --- role editor: publish_any and edit_any require read_drafts -------------------------------


def _role_post(c: TestClient, codes: list[str]) -> httpx.Response:
    response: httpx.Response = c.post(
        "/admin/roles",
        data={
            "name": f"rola-{uuid.uuid4().hex[:8]}",
            "codes": codes,
            "csrf_token": csrf(c, "/admin/roles/new"),
        },
        follow_redirects=False,
    )
    return response


@pytest.mark.parametrize("code", ["documents.publish_any", "documents.edit_any"])
def test_role_editor_requires_read_drafts(clients: dict[str, TestClient], code: str) -> None:
    admin = clients["admin"]
    response = _role_post(admin, ["documents.read", code])
    assert response.status_code == 400
    assert f"Uprawnienie {code} wymaga też documents.read_drafts." in response.text
    assert _role_post(admin, ["documents.read", "documents.read_drafts", code]).status_code == 303
    form = admin.get("/admin/roles/new").text
    assert "wymaga: documents.read_drafts" in form


def test_role_editor_refuses_removing_a_requirement(
    app: FastAPI, clients: dict[str, TestClient]
) -> None:
    admin = clients["admin"]
    created = _role_post(admin, ["documents.read_drafts", "documents.edit_any"])
    role_url = created.headers["location"].split("?")[0]
    response = admin.post(
        role_url,
        data={
            "name": f"zmiana-{uuid.uuid4().hex[:8]}",
            "codes": ["documents.edit_any"],
            "csrf_token": csrf(admin, role_url),
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "wymaga też documents.read_drafts" in response.text


def test_roles_stored_before_the_rule_are_flagged(
    app: FastAPI, clients: dict[str, TestClient]
) -> None:
    with app.state.session_factory.begin() as db:
        role = Role(name=f"stara-{uuid.uuid4().hex[:8]}")
        db.add(role)
        db.flush()
        db.add(RolePermission(role_id=role.id, permission_code="documents.publish_any"))
        role_id, name = role.id, role.name
    admin = clients["admin"]
    listing = admin.get(f"/admin/roles?q={name}").text
    assert name in listing and "niespójna" in listing
    page = admin.get(f"/admin/roles/{role_id}").text
    assert "Ta rola jest niespójna" in page
    assert "Uprawnienie documents.publish_any wymaga też documents.read_drafts." in page


def test_default_and_existing_system_roles_satisfy_the_requirements(app: FastAPI) -> None:
    requirements = app.state.permission_requirements
    with app.state.session_factory() as db:
        rows = db.execute(
            select(Role.system_key, RolePermission.permission_code)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .where(Role.system_key.is_not(None), Role.system_key != "admin")
        )
        held: dict[str, set[str]] = {}
        for key, code in rows:
            held.setdefault(key, set()).add(code)
    assert set(held) == {"publisher", "reader"}
    for key, codes in held.items():
        assert missing_requirements(codes, requirements) == {}, key


def test_sql_errors_never_put_the_value_into_the_log(app: FastAPI) -> None:
    """PostgreSQL quotes the offending value in its message; the log keeps only the type and
    SQLSTATE (ADR-025). SQLAlchemy does not add the bound parameters (hide_parameters)."""
    secret = "fraza-" + uuid.uuid4().hex
    with app.state.session_factory() as db, pytest.raises(Exception) as caught:
        db.execute(text("SELECT CAST(:value AS integer)"), {"value": secret})
    assert "[SQL parameters hidden due to hide_parameters=True]" in str(caught.value)
    record = logging.LogRecord(
        "baza_wiedzy.test", logging.ERROR, __file__, 1, "failed", None,
        (caught.type, caught.value, caught.tb),
    )  # fmt: skip
    line = json.loads(JsonFormatter().format(record))
    assert secret not in json.dumps(line)
    assert "sqlstate=22P02" in line["exc"]
    assert "InvalidTextRepresentation" in line["exc"] and "test_lifecycle.py" in line["exc"]
