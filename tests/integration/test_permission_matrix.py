"""Role x endpoint matrix, generated from the application's route list.

Every non-public route is called as: no session, Admin, Publisher, Reader and a custom role
without permissions. The expected answer comes from the route's access dependencies, so a route
added by a new module is covered automatically. Path parameters get random UUIDs (allowed calls
end in 404/400, never in a change of existing data); POST /logout is called last.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.permissions.service import effective_permissions
from baza_wiedzy.main import create_app
from route_helpers import allowed, protected_endpoints
from web_helpers import client, csrf, login, make_user

pytestmark = pytest.mark.integration

ROLES = {
    "admin": ("admin",),
    "publisher": ("publisher",),
    "reader": ("reader",),
    "custom_empty": (),
}


@pytest.fixture(scope="module")
def app(migrated: Settings) -> FastAPI:
    return create_app(
        migrated.model_copy(update={"db_pool_size": 3, "db_max_overflow": 0}),
        setup_logging=False,
    )


def _endpoints(app: FastAPI) -> list[tuple[str, str, set[str]]]:
    endpoints = protected_endpoints(app)
    assert len(endpoints) >= 20, "the route inventory looks empty"
    # Logging out ends the session, so it goes last for every client.
    return sorted(endpoints, key=lambda e: e[1] == "/logout")


def _call(c: TestClient, method: str, path: str) -> tuple[int, str]:
    url = re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), path)
    if method == "GET":
        response = c.get(url, follow_redirects=False)
    else:
        page = "/" if c.cookies.get("__Host-bw_session") else "/login"
        response = c.post(url, data={"csrf_token": csrf(c, page)}, follow_redirects=False)
    return response.status_code, response.headers.get("location", "")


@pytest.fixture(scope="module")
def clients(app: FastAPI) -> Iterator[dict[str, tuple[TestClient, frozenset[str]]]]:
    made: dict[str, tuple[TestClient, frozenset[str]]] = {}
    catalog: frozenset[str] = app.state.permission_catalog
    for scenario, roles in ROLES.items():
        user = make_user(app, *roles, custom_codes=set() if scenario == "custom_empty" else None)
        with app.state.session_factory() as db:
            _, codes = effective_permissions(db, user.id, catalog)
        made[scenario] = (login(app, user), codes)
    yield made
    for c, _ in made.values():
        c.__exit__(None, None, None)


def test_no_session_is_redirected_to_login(app: FastAPI) -> None:
    c = client(app)
    failures = []
    for method, path, _ in _endpoints(app):
        status, location = _call(c, method, path)
        if status != 303 or not location.startswith("/login?next="):
            failures.append(f"{method} {path}: {status} {location}")
    c.__exit__(None, None, None)
    assert not failures, failures


@pytest.mark.parametrize("scenario", list(ROLES))
def test_role_matrix(
    app: FastAPI, clients: dict[str, tuple[TestClient, frozenset[str]]], scenario: str
) -> None:
    c, permissions = clients[scenario]
    failures = []
    checked = 0
    for method, path, markers in _endpoints(app):
        status, location = _call(c, method, path)
        checked += 1
        if allowed(markers, permissions):
            # "Not logged in" redirects carry ?next=; /logout itself redirects to plain /login.
            if status in (401, 403) or location.startswith("/login?next="):
                failures.append(f"{method} {path}: expected access, got {status} {location}")
        elif status != 403:
            failures.append(f"{method} {path}: expected 403, got {status} {location}")
    assert checked >= 20
    assert not failures, f"{scenario}: {failures}"


def test_expected_shape_of_the_matrix(
    clients: dict[str, tuple[TestClient, frozenset[str]]],
) -> None:
    # Guards the test itself: the scenarios really differ.
    assert {"users.manage", "roles.manage", "audit.view"} <= clients["admin"][1]
    assert clients["reader"][1] == frozenset({"documents.read"})
    assert clients["custom_empty"][1] == frozenset()
