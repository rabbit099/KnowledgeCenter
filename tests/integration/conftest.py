"""Integration tests: real PostgreSQL (docker/db image) and Redis, through `make test-db`."""

from __future__ import annotations

import os

import pytest

from baza_wiedzy.core.config import Settings


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is not set; run `make test-db`")
    return value


@pytest.fixture
def settings() -> Settings:
    database_url = _env("TEST_DATABASE_URL").replace("postgresql://", "postgresql+psycopg://", 1)
    return Settings(  # type: ignore[call-arg]  # pydantic-settings init kwarg
        _env_file=None,
        database_url=database_url,
        redis_url=_env("TEST_REDIS_URL"),
        app_version="test",
        db_pool_size=1,
        db_max_overflow=0,
    )
