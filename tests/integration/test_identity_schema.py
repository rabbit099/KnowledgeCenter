"""Core identity and audit tables after `migrate` (core_0002, core_0003, documents_0001)."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.base import Base
from baza_wiedzy.core.db.models import AuditEntry  # noqa: F401  (registers the models)
from baza_wiedzy.modules.documents import models as document_models  # noqa: F401

pytestmark = pytest.mark.integration


def _connect(settings: Settings) -> psycopg.Connection[tuple[object, ...]]:
    url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(url)


def test_system_roles_and_default_grants(settings: Settings) -> None:
    with _connect(settings) as conn:
        rows = conn.execute(
            """
            SELECT r.system_key, coalesce(array_agg(p.permission_code ORDER BY p.permission_code)
                   FILTER (WHERE p.permission_code IS NOT NULL), '{}')
            FROM roles r LEFT JOIN role_permissions p ON p.role_id = r.id
            WHERE r.system_key IS NOT NULL GROUP BY r.system_key
            """
        ).fetchall()
    grants = {str(key): list(codes) for key, codes in rows}  # type: ignore[call-overload]
    assert grants == {
        "admin": [],  # the full catalog is computed in code
        "publisher": ["documents.publish", "documents.read", "documents.upload"],
        "reader": ["documents.read"],
    }


def test_audit_log_is_append_only(settings: Settings) -> None:
    marker = f"test-{uuid.uuid4().hex}"
    with _connect(settings) as conn:
        conn.execute(
            "INSERT INTO audit_log (actor_label, action) VALUES (%s, 'test.append')", (marker,)
        )
        conn.commit()
        for statement in (
            "UPDATE audit_log SET action = 'changed' WHERE actor_label = %s",
            "DELETE FROM audit_log WHERE actor_label = %s",
            "TRUNCATE audit_log",
        ):
            params = (marker,) if "%s" in statement else None
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
                conn.execute(statement, params)
            conn.rollback()
        row = conn.execute(
            "SELECT action FROM audit_log WHERE actor_label = %s", (marker,)
        ).fetchone()
    assert row == ("test.append",)


def test_local_user_needs_password_and_ad_user_must_not_have_one(settings: Settings) -> None:
    with _connect(settings) as conn:
        for source, password in (("local", None), ("ad", "argon2-hash")):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO users (username, source, password_hash) VALUES (%s, %s, %s)",
                    (f"u-{uuid.uuid4().hex}", source, password),
                )
            conn.rollback()


def test_username_is_unique_ignoring_case(settings: Settings) -> None:
    name = f"Jan.Kowalski-{uuid.uuid4().hex[:8]}"
    with _connect(settings) as conn:
        conn.execute("INSERT INTO users (username, source) VALUES (%s, 'ad')", (name,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO users (username, source) VALUES (%s, 'ad')", (name.upper(),))
        conn.rollback()


def test_models_match_migrations(settings: Settings) -> None:
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()
    # Tables that belong to no model yet (none so far) would show up here as "remove_table".
    assert diff == [], diff
