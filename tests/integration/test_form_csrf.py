"""Every POST route is submitted the way a browser does it: load the page, take the form the
user would submit (its own hidden csrf_token, or the HTMX header token for HTMX-only forms) and
send exactly that. Fails when a form renders without a token, and when a POST route has no case
here (the list is checked against the route inventory).

Regression for the admin forms rendering an empty csrf_token (macro imported without context).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.models import AdGroupRoleMap, Role
from baza_wiedzy.main import create_app
from baza_wiedzy.modules.documents.models import Document, Version
from baza_wiedzy.modules.ingestion import routes as ingestion_routes
from baza_wiedzy.modules.ingestion import tasks
from route_helpers import protected_endpoints
from web_helpers import PASSWORD, client, csrf, login, make_user

pytestmark = pytest.mark.integration

REFUSED = "Żądanie odrzucone"


@dataclass
class Form:
    method: str
    action: str
    htmx: bool
    multipart: bool
    fields: list[tuple[str, str]] = field(default_factory=list)

    def value(self, name: str) -> str | None:
        return next((v for n, v in self.fields if n == name), None)


class FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[Form] = []
        self.body_headers: dict[str, str] = {}
        self._select: str | None = None
        self._first_option: str | None = None
        self._selected = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "body" and "hx-headers" in a:
            self.body_headers = json.loads(a["hx-headers"])
        elif tag == "form":
            htmx = "hx-post" in a
            self.forms.append(
                Form(
                    method=(a.get("method") or ("post" if htmx else "get")).lower(),
                    action=a.get("hx-post") or a.get("action", ""),
                    htmx=htmx and a.get("method", "").lower() != "post",
                    multipart="multipart" in a.get("hx-encoding", a.get("enctype", "")),
                )
            )
        elif not self.forms:
            return
        elif tag == "input" and a.get("name"):
            kind = a.get("type", "text")
            if kind in ("checkbox", "radio") and "checked" not in a:
                return
            if kind not in ("submit", "button", "file"):
                self.forms[-1].fields.append((a["name"], a.get("value", "")))
        elif tag == "textarea" and a.get("name"):
            self.forms[-1].fields.append((a["name"], ""))
        elif tag == "select":
            self._select, self._first_option, self._selected = a.get("name"), None, False
        elif tag == "option" and self._select:
            if self._first_option is None:
                self._first_option = a.get("value", "")
            if "selected" in a:
                self.forms[-1].fields.append((self._select, a.get("value", "")))
                self._selected = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "select" and self._select:
            if not self._selected and self._first_option is not None:
                self.forms[-1].fields.append((self._select, self._first_option))
            self._select = None


def find_form(c: TestClient, page: str, action: str) -> tuple[Form, dict[str, str]]:
    response = c.get(page, headers={"HX-Request": "true"} if "/parts" in page else {})
    assert response.status_code == 200, (page, response.status_code)
    html = response.text
    if "<body" not in html:  # an HTMX fragment: the header token comes from the full page
        html = c.get("/").text + html
    parser = FormParser()
    parser.feed(html)
    forms = [f for f in parser.forms if f.method == "post" and f.action == action]
    assert forms, f"no POST form with action {action} on {page}"
    return forms[0], parser.body_headers


@dataclass
class Case:
    page: str
    action: str
    data: dict[str, str] = field(default_factory=dict)
    expect: tuple[int, ...] = (200, 204, 303)


@pytest.fixture
def app(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(tasks, "enqueue", lambda *a, **k: None)
    monkeypatch.setattr(ingestion_routes, "enqueue_ocr", lambda *a, **k: None)
    return create_app(settings.model_copy(update={"db_pool_size": 3}), setup_logging=False)


@pytest.fixture
def cases(app: FastAPI) -> dict[str, Callable[[], Case]]:
    target = make_user(app, "reader")
    publisher = make_user(app, "publisher")
    tag = uuid.uuid4().hex[:8]
    with app.state.session_factory.begin() as db:
        role = Role(name=f"rola-{tag}")
        doomed = Role(name=f"do-usuniecia-{tag}")
        db.add_all([role, doomed])
        db.flush()
        mapping = AdGroupRoleMap(
            ad_group_dn=f"CN=G-{tag},OU=Groups,DC=example,DC=com", role_id=role.id
        )
        db.add(mapping)
        db.flush()
        role_id, doomed_id, mapping_id = role.id, doomed.id, mapping.id
    up = login(app, publisher)
    text = f"tekst testowy {tag}".encode()  # unique: earlier cases publish their documents
    response = up.post(
        "/documents/upload",
        files={"file": ("a.txt", text, "text/plain")},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf(up, "/documents/upload")},
        follow_redirects=False,
    )
    doc_id = uuid.UUID(response.headers["hx-redirect"].split("/")[2].split("?")[0])
    with app.state.session_factory() as db:
        version_id = db.scalar(select(Document.latest_version_id).where(Document.id == doc_id))
    # Two files, the first identical to a.txt: held as a possible duplicate (row 0).
    batch = up.post(
        "/documents/upload",
        files=[
            ("files", ("kopia.txt", text, "text/plain")),
            ("files", ("nowy.txt", uuid.uuid4().hex.encode(), "text/plain")),
        ],
        headers={"HX-Request": "true", "X-CSRF-Token": csrf(up, "/documents/upload")},
        follow_redirects=False,
    )
    batch_id = batch.headers["hx-redirect"].split("batch=")[1]
    # Two scanned versions waiting for OCR: one on hold ("Uruchom OCR"), one queued ("Anuluj").
    ocr_docs: dict[str, uuid.UUID] = {}
    for state in ("on_hold", "queued"):
        response = up.post(
            "/documents/upload",
            files={"file": (f"{state}.txt", f"{state} {tag}".encode(), "text/plain")},
            headers={"HX-Request": "true", "X-CSRF-Token": csrf(up, "/documents/upload")},
            follow_redirects=False,
        )
        ocr_doc = uuid.UUID(response.headers["hx-redirect"].split("/")[2].split("?")[0])
        with app.state.session_factory.begin() as db:
            document = db.get(Document, ocr_doc)
            assert document is not None
            version = db.get(Version, document.latest_version_id)
            assert version is not None
            version.processing_status, version.ocr_status, version.ocr_pages = "needs_ocr", state, 2
            ocr_docs[state] = version.id
        ocr_docs[f"{state}-doc"] = ocr_doc
    app.state.publisher = publisher
    return {
        "/login": lambda: Case(
            "/login", "/login", {"username": target.username, "password": PASSWORD}
        ),
        "/logout": lambda: Case("/", "/logout"),
        "/admin/users": lambda: Case(
            "/admin/users/new", "/admin/users", {"username": f"ad-{uuid.uuid4().hex[:8]}"}
        ),
        "/admin/users/{user_id}/roles": lambda: Case(
            f"/admin/users/{target.id}", f"/admin/users/{target.id}/roles"
        ),
        "/admin/users/{user_id}/active": lambda: Case(
            f"/admin/users/{target.id}", f"/admin/users/{target.id}/active"
        ),
        "/admin/users/{user_id}/sessions": lambda: Case(
            f"/admin/users/{target.id}", f"/admin/users/{target.id}/sessions"
        ),
        "/admin/roles": lambda: Case(
            "/admin/roles/new", "/admin/roles", {"name": f"nowa-{uuid.uuid4().hex[:8]}"}
        ),
        "/admin/roles/{role_id}": lambda: Case(
            f"/admin/roles/{role_id}", f"/admin/roles/{role_id}"
        ),
        "/admin/roles/{role_id}/delete": lambda: Case(
            f"/admin/roles/{doomed_id}/delete", f"/admin/roles/{doomed_id}/delete"
        ),
        "/admin/ad-groups": lambda: Case(
            "/admin/ad-groups",
            "/admin/ad-groups",
            {
                "dn": f"CN=N-{uuid.uuid4().hex[:8]},OU=Groups,DC=example,DC=com",
                "role_id": str(role_id),
            },
        ),
        "/admin/ad-groups/{mapping_id}/delete": lambda: Case(
            "/admin/ad-groups", f"/admin/ad-groups/{mapping_id}/delete"
        ),
        "/admin/audit/export": lambda: Case("/admin/audit", "/admin/audit/export"),
        "/documents/upload": lambda: Case("/documents/upload", "/documents/upload"),
        "/documents/upload/batches/{batch_id}/{row}": lambda: Case(
            f"/documents?batch={batch_id}", f"/documents/upload/batches/{batch_id}/0"
        ),
        "/ingestion/versions/{version_id}/ocr": lambda: Case(
            f"/documents/{ocr_docs['on_hold-doc']}",
            f"/ingestion/versions/{ocr_docs['on_hold']}/ocr",
        ),
        "/ingestion/versions/{version_id}/ocr/cancel": lambda: Case(
            f"/documents/{ocr_docs['queued-doc']}",
            f"/ingestion/versions/{ocr_docs['queued']}/ocr/cancel",
        ),
        "/ingestion/versions/{version_id}/reprocess": lambda: Case(
            f"/ingestion/versions/{version_id}/parts", f"/ingestion/versions/{version_id}/reprocess"
        ),
        "/documents/{document_id}/lifecycle": lambda: Case(
            f"/documents/{doc_id}", f"/documents/{doc_id}/lifecycle"
        ),
    }


def test_every_post_route_has_a_form_case(
    app: FastAPI, cases: dict[str, Callable[[], Case]]
) -> None:
    posts = {path for method, path, _ in protected_endpoints(app) if method == "POST"}
    assert set(cases) == posts | {"/login"}


ROUTES = [
    "/login",
    "/admin/users",
    "/admin/users/{user_id}/roles",
    "/admin/users/{user_id}/active",
    "/admin/users/{user_id}/sessions",
    "/admin/roles",
    "/admin/roles/{role_id}",
    "/admin/roles/{role_id}/delete",
    "/admin/ad-groups",
    "/admin/ad-groups/{mapping_id}/delete",
    "/admin/audit/export",
    "/documents/upload",
    "/documents/upload/batches/{batch_id}/{row}",
    "/ingestion/versions/{version_id}/reprocess",
    "/ingestion/versions/{version_id}/ocr",
    "/ingestion/versions/{version_id}/ocr/cancel",
    "/documents/{document_id}/lifecycle",
    "/logout",
]


@pytest.mark.parametrize("route", ROUTES)
def test_form_submits_with_the_token_it_renders(
    app: FastAPI, cases: dict[str, Callable[[], Case]], route: str
) -> None:
    case = cases[route]()
    if route == "/login":
        c = client(app)
    elif route in (
        "/documents/upload",
        "/documents/upload/batches/{batch_id}/{row}",
        "/ingestion/versions/{version_id}/reprocess",
        "/ingestion/versions/{version_id}/ocr",
        "/ingestion/versions/{version_id}/ocr/cancel",
        "/documents/{document_id}/lifecycle",
    ):
        c = login(app, app.state.publisher)
    else:
        c = login(app, make_user(app, "admin"))
    form, body_headers = find_form(c, case.page, case.action)
    data = [(n, v) for n, v in form.fields if n not in case.data] + list(case.data.items())
    headers: dict[str, str] = {}
    if form.htmx:
        token = body_headers.get("X-CSRF-Token", "")
        assert token, f"{case.page}: HTMX form without the header token on <body>"
        headers = {"HX-Request": "true", "X-CSRF-Token": token}
        assert "csrf_token" not in dict(data) or dict(data)["csrf_token"]
    else:
        assert form.value("csrf_token"), f"{case.page}: form {case.action} has no csrf_token value"
    if form.multipart:
        response: httpx.Response = c.post(
            case.action,
            files={"file": ("b.txt", b"inny tekst", "text/plain")},
            data=dict(data),
            headers=headers,
            follow_redirects=False,
        )
    else:
        grouped: dict[str, list[str]] = {}
        for name, value in data:
            grouped.setdefault(name, []).append(value)
        response = c.post(case.action, data=grouped, headers=headers, follow_redirects=False)
    assert response.status_code != 403 and REFUSED not in response.text, (
        route,
        response.text[:300],
    )
    assert response.status_code in case.expect, (route, response.status_code, response.text[:300])


def test_refused_form_shows_the_application_page_and_logs_the_reason(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    c = login(app, make_user(app, "admin"))
    token = csrf(c, "/")
    response = c.post("/admin/roles", data={"name": "x", "csrf_token": "0" * 64})
    assert response.status_code == 403
    page = response.text
    assert "<html" in page and REFUSED in page
    assert 'href="/login"' in page and "Zaloguj ponownie" in page
    assert (
        "CSRF check refused a request: reason=mismatch method=POST path=/admin/roles" in caplog.text
    )
    assert token not in caplog.text and "0" * 64 not in caplog.text
    assert re.search(r"reason=missing_token", caplog.text) is None
    c.post("/admin/roles", data={"name": "x"})
    assert "reason=missing_token" in caplog.text
