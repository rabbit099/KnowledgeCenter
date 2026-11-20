"""Engine creation. One engine per process, with a small fixed pool (ADR-016)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from baza_wiedzy.core.config import Settings


def make_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=3600,
        # Exception messages never carry bound parameters: they can hold search phrases and
        # document text (errors are logged; CLAUDE.md 8, ADR-025).
        hide_parameters=True,
        connect_args={"connect_timeout": 3, "application_name": "baza-wiedzy"},
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False: objects stay readable after commit (templates render them).
    return sessionmaker(bind=engine, expire_on_commit=False)
