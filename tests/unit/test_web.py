"""Web application without external services: checks are replaced by stubs, no login needed."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from babel.messages.extract import extract_from_dir
from babel.messages.pofile import read_po
from fastapi import FastAPI
from fastapi.testclient import TestClient

from baza_wiedzy import __version__
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.health import CheckResult
from baza_wiedzy.core.web.i18n import LOCALE_DIR
from baza_wiedzy.main import ConfigurationError, create_app

SRC = LOCALE_DIR.parents[3]  # .../src
SECRET = "unit-test-secret-key-0123456789abcdef"


def make_settings(**overrides: object) -> Settings:
    values: dict[str, Any] = {
        "app_name": "Testowa Baza",
        "enabled_modules": "documents,ingestion,search",
        "secret_key": SECRET,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]  # pydantic-settings init kwarg


@pytest.fixture
def app() -> FastAPI:
    app = create_app(make_settings(), setup_logging=False)
    app.state.ready_checks = {
        "database": lambda: CheckResult(True),
        "redis": lambda: CheckResult(True),
    }
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_ready_ok(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "redis": "ok"}


def test_ready_fails_when_a_check_fails(app: FastAPI, client: TestClient) -> None:
    app.state.ready_checks["redis"] = lambda: CheckResult(False, "unreachable")
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "fail"
    assert response.json()["checks"]["redis"] == "unreachable"


@pytest.mark.parametrize("path", ["/", "/status"])
def test_pages_need_a_session(client: TestClient, path: str) -> None:
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next={path}"


def test_htmx_request_without_session_gets_hx_redirect(client: TestClient) -> None:
    response = client.get("/status", headers={"HX-Request": "true"}, follow_redirects=False)
    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/login?next=/status"


def test_login_page_is_polish_with_csrf_field(client: TestClient) -> None:
    response = client.get("/login")
    html = response.text
    assert '<html lang="pl">' in html
    assert "Zaloguj się · Testowa Baza" in html
    assert f"Wersja {__version__}" in html
    assert 'name="csrf_token"' in html
    assert "__Host-bw_pre=" in response.headers["set-cookie"]


def test_app_name_is_escaped() -> None:
    app = create_app(make_settings(app_name="<b>Baza</b>"), setup_logging=False)
    with TestClient(app, base_url="https://testserver") as client:
        html = client.get("/login").text
    assert "<b>Baza</b>" not in html
    assert "&lt;b&gt;Baza&lt;/b&gt;" in html


def test_htmx_is_served_locally_with_strict_config(client: TestClient) -> None:
    html = client.get("/login").text
    assert 'src="http' not in html  # no CDN
    assert "/static/vendor/htmx-2.0.11.min.js" in html
    assert '"allowEval": false' in html and '"includeIndicatorStyles": false' in html
    assert client.get("/static/vendor/htmx-2.0.11.min.js").status_code == 200


def test_security_headers(client: TestClient) -> None:
    response = client.get("/login")
    csp = response.headers["content-security-policy"]
    for directive in (
        "default-src 'self'",
        "script-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ):
        assert directive in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert "cache-control" not in client.get("/static/css/app.css").headers


@pytest.mark.parametrize("secret", ["", "change-me", "x" * 31])
def test_weak_secret_key_refuses_to_start(secret: str) -> None:
    with pytest.raises(ConfigurationError, match="SECRET_KEY"):
        create_app(make_settings(secret_key=secret), setup_logging=False)


def test_every_ui_string_has_a_polish_translation() -> None:
    method_map = [("**.py", "python"), ("**/templates/**.html", "jinja2")]
    options = {"**/templates/**.html": {"extensions": "jinja2.ext.i18n"}}
    extracted = {
        msg if isinstance(msg, str) else msg[0]
        for _, _, msg, _, _ in extract_from_dir(str(SRC), method_map, options, callback=None)
    }
    with (LOCALE_DIR / "pl" / "LC_MESSAGES" / "messages.po").open("rb") as po_file:
        catalog = read_po(po_file, locale="pl")
    translated = {
        m.id[0] if isinstance(m.id, tuple) else m.id
        for m in catalog
        if m.id and m.string and not m.fuzzy and (not isinstance(m.string, tuple) or all(m.string))
    }
    assert extracted, "no strings extracted; check the extraction setup"
    assert extracted <= translated, f"untranslated: {sorted(extracted - translated)}"


@pytest.mark.parametrize(
    ("size", "text"),
    [(0, "0 B"), (17, "17 B"), (1023, "1023 B"), (1536, "1,5 KB"), (5 * 1024**2, "5,0 MB")],
)
def test_file_sizes_are_readable(size: int, text: str) -> None:
    from baza_wiedzy.core.web.templating import filesize

    assert filesize(size) == text


def test_long_hashes_are_recognised_for_shortening() -> None:
    from baza_wiedzy.core.web.templating import is_hash

    assert is_hash("a" * 64) and not is_hash("abc") and not is_hash("Z" * 64) and not is_hash(5)
