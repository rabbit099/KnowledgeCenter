"""Worker startup: wait for migrations before the startup hooks."""

from __future__ import annotations

import threading

import pytest

from baza_wiedzy.core.health import CheckResult
from baza_wiedzy.core.runtime import wait_for_schema


def test_waits_until_the_schema_is_migrated(caplog: pytest.LogCaptureFixture) -> None:
    answers = iter(
        [CheckResult(False, "not migrated"), CheckResult(False, "outdated"), CheckResult(True)]
    )
    calls: list[int] = []

    def check() -> CheckResult:
        calls.append(1)
        return next(answers)

    assert wait_for_schema(check, stop=threading.Event(), interval=0, log_every=3600)
    assert len(calls) == 3
    # One warning (then at most one per log_every), with the command to run.
    assert caplog.text.count("waiting for database migrations") == 1
    assert "schema: not migrated" in caplog.text and "baza-wiedzy migrate" in caplog.text


def test_a_newer_schema_counts_as_ready() -> None:
    def check() -> CheckResult:
        return CheckResult(True, "ok (schema newer than application)")

    assert wait_for_schema(check, stop=threading.Event(), interval=0)


def test_stops_waiting_when_the_worker_shuts_down() -> None:
    stop = threading.Event()

    def check() -> CheckResult:
        stop.set()
        return CheckResult(False, "not migrated")

    assert not wait_for_schema(check, stop=stop, interval=0)
