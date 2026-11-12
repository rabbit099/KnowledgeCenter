"""Migration plumbing that needs no database."""

from __future__ import annotations

import pytest

from baza_wiedzy.cli import main
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.migrate import (
    all_manifests,
    expected_heads,
    make_config,
    schema_status,
)


def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]  # pydantic-settings init kwarg


def test_all_manifests_include_disabled_modules() -> None:
    names = [m.name for m in all_manifests()]
    assert names[0] == "core"
    assert {"documents", "ingestion", "search"} <= set(names)


def test_expected_heads_are_all_script_heads() -> None:
    # Independent of ENABLED_MODULES: the schema is always complete.
    assert expected_heads(make_config(settings())) == {"core_0001", "search_0001"}


@pytest.mark.parametrize(
    ("current", "applied", "unknown", "ok", "detail"),
    [
        (set(), set(), set(), False, "not migrated"),
        ({"core_0001"}, {"core_0001"}, set(), False, "outdated"),
        ({"core_0001", "search_0001"}, {"core_0001", "search_0001"}, set(), True, "ok"),
        ({"search_0002"}, set(), {"search_0002"}, True, "ok (schema newer than application)"),
    ],
)
def test_schema_status(
    current: set[str], applied: set[str], unknown: set[str], ok: bool, detail: str
) -> None:
    result = schema_status({"core_0001", "search_0001"}, current, applied, unknown)
    assert (result.ok, result.detail) == (ok, detail)


def test_revision_unknown_branch_is_a_readable_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["revision", "--branch", "nope", "-m", "x"]) == 1
    assert "known branches: core, search" in capsys.readouterr().err
