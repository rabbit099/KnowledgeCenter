"""Engine creation. One engine per process, with a small fixed pool (ADR-016)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

from baza_wiedzy.core.config import Settings


def make_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={"connect_timeout": 3, "application_name": "baza-wiedzy"},
    )
