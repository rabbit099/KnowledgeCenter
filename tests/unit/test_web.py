"""Web application without external services: checks are replaced by stubs."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from babel.messages.extract import extract_from_dir
from babel.messages.pofile import read_po
from fastapi import FastAPI
from fastapi.testclient import TestClient

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.health import CheckResult
from baza_wiedzy.core.web.i18n import LOCALE_DIR
from baza_wiedzy.main import create_app

SRC = LOCALE_DIR.parents[3]  # .../src


def make_settings(**overrides: object) -> Settings:
    values: dict[str, Any] = {
        "app_name": "Testowa Baza",
        "app_version": "9.9.9",
        "enabled_modules": "documents,ingestion,search",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]  # pydantic-settings init kwarg


@pytest.fixture
def app() -> FastAPI:
    app = create_app(make_settings())
    app.state.ready_checks = {
        "database": lambda: CheckResult(True),
        "redis": lambda: CheckResult(True),
    }
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "9.9.9"}


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


def test_home_is_polish_and_uses_app_name(client: TestClient) -> None:
    html = client.get("/").text
    assert '<html lang="pl">' in html
    assert "<title>Testowa Baza</title>" in html
    assert "Firmowa baza wiedzy jest w przygotowaniu" in html
    assert "Wersja 9.9.9" in html


def test_app_name_is_escaped() -> None:
    app = create_app(make_settings(app_name="<b>Baza</b>"))
    with TestClient(app) as client:
        html = client.get("/").text
    assert "<b>Baza</b>" not in html
    assert "&lt;b&gt;Baza&lt;/b&gt;" in html


def test_htmx_is_served_locally(client: TestClient) -> None:
    html = client.get("/").text
    assert 'src="http' not in html  # no CDN
    assert "/static/vendor/htmx-2.0.11.min.js" in html
    assert client.get("/static/vendor/htmx-2.0.11.min.js").status_code == 200


def test_status_fragment(app: FastAPI, client: TestClient) -> None:
    app.state.ready_checks["database"] = lambda: CheckResult(False, "unreachable")
    html = client.get("/status").text
    assert "Niektóre usługi nie działają." in html
    assert "Baza danych: unreachable" in html
    assert "Kolejka zadań: OK" in html


def test_every_ui_string_has_a_polish_translation() -> None:
    method_map = [("**.py", "python"), ("**/templates/**.html", "jinja2")]
    options = {"**/templates/**.html": {"extensions": "jinja2.ext.i18n"}}
    extracted = {
        msg if isinstance(msg, str) else msg[0]
        for _, _, msg, _, _ in extract_from_dir(str(SRC), method_map, options, callback=None)
    }
    with (LOCALE_DIR / "pl" / "LC_MESSAGES" / "messages.po").open("rb") as po_file:
        catalog = read_po(po_file, locale="pl")
    translated = {m.id for m in catalog if m.id and m.string and not m.fuzzy}
    assert extracted, "no strings extracted; check the extraction setup"
    assert extracted <= translated, f"untranslated: {sorted(extracted - translated)}"
