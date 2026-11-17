"""Security properties of the web layer (see also test_login.py: fixation, CSRF on login and
logout, expiry; test_permission_matrix.py: role x endpoint)."""

from __future__ import annotations

import statistics
import time
import uuid

import pytest
from fastapi import FastAPI

from baza_wiedzy.core.config import Settings
from baza_wiedzy.main import create_app
from web_helpers import PASSWORD, client, csrf, login, make_user

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def app(migrated: Settings) -> FastAPI:
    return create_app(
        migrated.model_copy(update={"db_pool_size": 3, "db_max_overflow": 0}),
        setup_logging=False,
    )


def _attempt(app: FastAPI, username: str) -> tuple[float, int, str]:
    c = client(app)  # own address: the per-address limit never interferes
    token = csrf(c)
    started = time.perf_counter()
    response = c.post(
        "/login",
        data={"username": username, "password": "wrong password value", "csrf_token": token},
        follow_redirects=False,
    )
    elapsed = time.perf_counter() - started
    c.__exit__(None, None, None)
    body = response.text.replace(token, "TOKEN")
    return elapsed, response.status_code, body


def test_failed_login_does_not_reveal_the_account_by_content_or_time(app: FastAPI) -> None:
    existing = make_user(app, "reader")
    known = [_attempt(app, existing.username) for _ in range(7)]
    unknown = [_attempt(app, f"nobody-{uuid.uuid4().hex[:8]}") for _ in range(7)]
    assert {s for _, s, _ in known + unknown} == {401}
    assert len({b for _, _, b in known + unknown}) == 1  # byte-identical pages
    known_ms = statistics.median(t for t, _, _ in known)
    unknown_ms = statistics.median(t for t, _, _ in unknown)
    # Both paths compute one Argon2 hash; a missing account must not answer visibly faster.
    assert abs(known_ms - unknown_ms) < 0.5 * max(known_ms, unknown_ms), (known_ms, unknown_ms)


def test_foreign_origin_is_refused_even_with_a_valid_token(app: FastAPI) -> None:
    user = make_user(app, "admin")
    c = client(app)
    token = csrf(c)
    response = c.post(
        "/login",
        data={"username": user.username, "password": PASSWORD, "csrf_token": token},
        headers={"Origin": "https://evil.example"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    admin = login(app, user)
    response = admin.post(
        "/admin/roles",
        data={"name": "x", "csrf_token": csrf(admin, "/admin/roles")},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


def test_state_changing_admin_requests_need_csrf(app: FastAPI) -> None:
    admin = login(app, make_user(app, "admin"))
    for path in ("/admin/roles", "/admin/users", "/admin/ad-groups", "/admin/audit/export"):
        assert admin.post(path, data={}).status_code == 403, path


@pytest.mark.parametrize("path", ["/login", "/admin/users", "/admin/no-such-page", "/admin/audit"])
def test_security_headers_on_pages_and_errors(app: FastAPI, path: str) -> None:
    c = login(app, make_user(app, "reader"))  # also covers 403 and 404 pages
    response = c.get(path, follow_redirects=False)
    headers = response.headers
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "unsafe-inline" not in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "same-origin"
    assert headers["cache-control"] == "no-store"
