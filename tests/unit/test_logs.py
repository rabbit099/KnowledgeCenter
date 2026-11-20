"""JSON log lines."""

from __future__ import annotations

import io
import json
import logging

from baza_wiedzy.core.logs import configure_logging


def test_records_are_json_lines() -> None:
    stream = io.StringIO()
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        configure_logging("INFO", stream=stream)
        logging.getLogger("uvicorn.access").info('%s - "%s %s"', "10.0.0.1", "GET", "/ready")
        try:
            raise ValueError("zażółć")
        except ValueError:
            logging.getLogger("baza_wiedzy.test").exception("failed")
        logging.getLogger("baza_wiedzy.test").debug("not shown at INFO")
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(lines) == 2
    assert lines[0]["logger"] == "uvicorn.access"
    assert lines[0]["message"] == '10.0.0.1 - "GET /ready"'
    assert lines[0]["level"] == "INFO"
    assert lines[0]["ts"].endswith("+00:00")
    assert lines[1]["level"] == "ERROR"
    assert "zażółć" in lines[1]["exc"]


def test_access_log_never_contains_the_query_string() -> None:
    """Search phrases travel in the URL; the access log keeps only the path (ADR-025)."""
    stream = io.StringIO()
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        configure_logging("INFO", stream=stream)
        access = logging.getLogger("uvicorn.access")
        access.info(
            '%s - "%s %s HTTP/%s" %d',
            "10.0.0.1",
            "GET",
            "/search?q=tajna+fraza+%C5%BC%C3%B3%C5%82w&page=2",
            "1.1",
            200,
        )
        access.info('%s - "%s %s HTTP/%s" %d', "10.0.0.1", "GET", "/documents", "1.1", 200)
        logging.getLogger("baza_wiedzy.test").info("other %s", "/x?y=1")
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
    text = stream.getvalue()
    messages = [json.loads(line)["message"] for line in text.splitlines()]
    assert messages[0] == '10.0.0.1 - "GET /search?… HTTP/1.1" 200'
    assert messages[1] == '10.0.0.1 - "GET /documents HTTP/1.1" 200'
    assert messages[2] == "other /x?y=1"  # only uvicorn access records are rewritten
    assert "tajna" not in text and "%C5%BC" not in text and "page=2" not in text


def test_sql_errors_do_not_carry_bound_parameters() -> None:
    from baza_wiedzy.core.config import Settings
    from baza_wiedzy.core.db.session import make_engine

    engine = make_engine(
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            database_url="postgresql+psycopg://u:p@127.0.0.1:1/x",
            secret_key="unit-test-secret-key-0123456789abcdef",
        )
    )
    assert engine.hide_parameters is True


def test_database_exception_messages_are_hidden_in_the_log() -> None:
    from baza_wiedzy.core.logs import JsonFormatter

    class FakeDbError(Exception):
        sqlstate = "22P02"

    FakeDbError.__module__ = "psycopg.errors"

    def fail() -> None:
        try:
            raise ValueError("ordinary message stays")
        except ValueError as inner:
            # Built at run time: traceback source lines must not contain the phrase themselves.
            raise FakeDbError("invalid input: " + " ".join(["tajna", "fraza"])) from inner

    try:
        fail()
    except FakeDbError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    entry = json.loads(JsonFormatter().format(record))
    assert "tajna fraza" not in entry["exc"]
    assert "psycopg.errors" in entry["exc"] and "sqlstate=22P02" in entry["exc"]
    assert "ValueError: ordinary message stays" in entry["exc"]
    assert "direct cause" in entry["exc"]
    assert entry["exc"].index("ValueError") < entry["exc"].index("sqlstate")
