"""Logging: one JSON object per line on stdout/stderr, collected by Docker (CLAUDE.md, ops docs).

Fields: ts (UTC, ISO 8601), level, logger, message, process, and exc (traceback) when present.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import TextIO

UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "process": record.process,
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Route all records through the root logger with a JSON handler.

    Existing root handlers are replaced (in Dramatiq worker processes the stream is the pipe
    Dramatiq set up, so output still reaches the container log). Uvicorn's loggers lose their
    own text handlers and propagate to the root instead.
    """
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level.upper())
    for name in UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    # /ready reads the migration state on every call; Alembic would log two INFO lines each time.
    logging.getLogger("alembic").setLevel(logging.WARNING)
