"""Logging: one JSON object per line on stdout/stderr, collected by Docker (CLAUDE.md, ops docs).

Fields: ts (UTC, ISO 8601), level, logger, message, process, and exc (traceback) when present.

Privacy: the access log of uvicorn never contains query strings (`DropQueryString`): search
phrases and list filters travel in the URL (`/search?q=...`) and must not reach any log
(the proxy log drops them too, docker/caddy/Caddyfile; ADR-025). Database exceptions are
logged without their message (`safe_exception`): PostgreSQL quotes the offending value in it
(for example `invalid input syntax for type integer: "<value>"`), and that value can be a
search phrase or document text; the type, SQLSTATE and the code frames remain.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from types import TracebackType
from typing import TextIO

UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class DropQueryString(logging.Filter):
    """Removes the query string from uvicorn access records (args: client, method, path,
    http version, status). Records of other loggers pass unchanged."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple):
            args = list(record.args)
            if len(args) > 2 and isinstance(args[2], str) and "?" in args[2]:
                args[2] = args[2].split("?", 1)[0] + "?…"
                record.args = tuple(args)
        return True


_HIDDEN_MESSAGE_PACKAGES = frozenset({"psycopg", "sqlalchemy"})
ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def _hides_message(exc: BaseException) -> bool:
    return type(exc).__module__.split(".", 1)[0] in _HIDDEN_MESSAGE_PACKAGES


def _only(exc: BaseException) -> str:
    name = f"{type(exc).__module__}.{type(exc).__qualname__}"
    if not _hides_message(exc):
        return "".join(traceback.format_exception_only(type(exc), exc))
    sqlstate = getattr(exc, "sqlstate", None) or getattr(
        getattr(exc, "orig", None), "sqlstate", None
    )
    return f"{name}: [message hidden: database error, sqlstate={sqlstate or 'unknown'}]\n"


def safe_exception(exc_info: ExcInfo) -> str:
    """Like logging's traceback text, but database exceptions lose their message."""
    chain: list[tuple[BaseException, str]] = []
    exc: BaseException | None = exc_info[1]
    link = ""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        chain.append((exc, link))
        if exc.__cause__ is not None:
            exc, link = exc.__cause__, "direct cause"
        elif exc.__context__ is not None and not exc.__suppress_context__:
            exc, link = exc.__context__, "context"
        else:
            exc = None
    out: list[str] = []
    for exc, link in reversed(chain):
        if exc.__traceback__ is not None:
            out.append("Traceback (most recent call last):\n")
            out.extend(traceback.format_tb(exc.__traceback__))
        out.append(_only(exc))
        if link == "direct cause":
            out.append("\nThe above exception was the direct cause of the following exception:\n\n")
        elif link == "context":
            out.append("\nDuring handling of the above exception, another exception occurred:\n\n")
    return "".join(out).rstrip("\n")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "process": record.process,
        }
        if record.exc_info and record.exc_info[1] is not None:
            entry["exc"] = safe_exception(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Route all records through the root logger with a JSON handler.

    Existing root handlers are replaced (in Dramatiq worker processes the stream is the pipe
    Dramatiq set up, so output still reaches the container log). Uvicorn's loggers lose their
    own text handlers and propagate to the root instead.
    """
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(DropQueryString())
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
