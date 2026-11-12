"""`baza-wiedzy migrate` against the real database, as the non-superuser application role."""

from __future__ import annotations

import psycopg
import pytest
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.migrate import applied_revisions, make_config, upgrade
from baza_wiedzy.main import create_app

pytestmark = pytest.mark.integration


def _connect(settings: Settings) -> psycopg.Connection[tuple[object, ...]]:
    url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(url, autocommit=True)


def test_runs_as_non_superuser(settings: Settings) -> None:
    with _connect(settings) as conn:
        row = conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
    assert row == (False,)


def test_creates_extensions_dictionary_and_configurations(settings: Settings) -> None:
    with _connect(settings) as conn:
        extensions = {r[0] for r in conn.execute("SELECT extname FROM pg_extension").fetchall()}
        configs = {r[0] for r in conn.execute("SELECT cfgname FROM pg_ts_config").fetchall()}
        lexemes = conn.execute("SELECT ts_lexize('pl_hunspell', 'fakturze')").fetchone()
        versions = {str(r[0]) for r in conn.execute("SELECT version_num FROM alembic_version")}
    assert {"pg_trgm", "unaccent"} <= extensions
    assert {"pl", "simple_unaccent", "english"} <= configs
    assert lexemes == (["faktura"],)
    # alembic_version keeps only heads; core_0001 is implied by search_0001's depends_on.
    script = ScriptDirectory.from_config(make_config(settings))
    assert applied_revisions(script, versions) == {"core_0001", "search_0001"}


def test_second_run_changes_nothing(settings: Settings) -> None:
    with _connect(settings) as conn:
        before = {r[0] for r in conn.execute("SELECT version_num FROM alembic_version")}
    upgrade(settings)
    with _connect(settings) as conn:
        after = {r[0] for r in conn.execute("SELECT version_num FROM alembic_version")}
    assert before == after == {"search_0001"}


def test_partial_module_set_still_migrates_everything(settings: Settings) -> None:
    only_documents = settings.model_copy(update={"enabled_modules": ("documents",)})
    assert upgrade(only_documents) == ["core_0001", "search_0001"]
    with TestClient(create_app(only_documents, setup_logging=False)) as client:
        assert client.get("/ready").json()["checks"]["schema"] == "ok"


def test_ready_reports_schema_ok(settings: Settings) -> None:
    with TestClient(create_app(settings, setup_logging=False)) as client:
        checks = client.get("/ready").json()["checks"]
    assert checks["schema"] == "ok"
