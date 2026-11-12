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
