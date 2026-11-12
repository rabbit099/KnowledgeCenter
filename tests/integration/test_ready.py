"""/ready against the real database and Redis."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from baza_wiedzy.core.config import Settings
from baza_wiedzy.main import create_app

pytestmark = pytest.mark.integration


def test_database_and_redis_are_ready(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        checks = client.get("/ready").json()["checks"]
    assert checks["database"] == "ok"
    assert checks["redis"] == "ok"


def test_wrong_redis_password_is_reported(settings: Settings) -> None:
    bad = settings.model_copy(update={"redis_url": settings.redis_url.replace(":test@", ":wrong@")})
    with TestClient(create_app(bad)) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "authentication failed"


def test_unreachable_database_is_reported(settings: Settings) -> None:
    bad = settings.model_copy(
        update={"database_url": settings.database_url.replace("@db-test:", "@nonexistent-host:")}
    )
    with TestClient(create_app(bad)) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unreachable"
