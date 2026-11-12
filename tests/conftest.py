"""Shared fixtures. Database fixtures need `make test-db` (TEST_DATABASE_URL, TEST_REDIS_URL)."""

from __future__ import annotations

import os

import dramatiq
import pytest
from dramatiq.brokers.stub import StubBroker

from baza_wiedzy.core.config import Settings

# Actors are declared at import time against the global broker; tests never talk to a real
# broker unless they bind the actors to one explicitly (baza_wiedzy.core.queue.setup_broker).
dramatiq.set_broker(StubBroker())


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is not set; run `make test-db`")
    return value


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    database_url = _env("TEST_DATABASE_URL").replace("postgresql://", "postgresql+psycopg://", 1)
    return Settings(  # type: ignore[call-arg]  # pydantic-settings init kwarg
        _env_file=None,
        database_url=database_url,
        redis_url=_env("TEST_REDIS_URL"),
        app_version="test",
        db_pool_size=1,
        db_max_overflow=0,
    )


@pytest.fixture(scope="session")
def migrated(test_settings: Settings) -> Settings:
    """The test database after `baza-wiedzy migrate` (run once per test session)."""
    from baza_wiedzy.core.db.migrate import upgrade

    upgrade(test_settings)
    return test_settings
