"""Integration tests: real PostgreSQL (docker/db image) and Redis, through `make test-db`."""

from __future__ import annotations

import pytest

from baza_wiedzy.core.config import Settings


@pytest.fixture
def settings(migrated: Settings) -> Settings:
    return migrated
