"""The search engine interface (CLAUDE.md 6: the engine can be replaced without touching the
rest) and its request and result types. The PostgreSQL implementation is postgres.py."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from markupsafe import Markup
from sqlalchemy.orm import Session

from baza_wiedzy.core.permissions.service import CurrentUser

SORTS = ("relevance", "date")
LANGUAGES = ("pl", "en", "unknown")


class QueryProblem(Exception):
    """The query cannot be run as typed. message: English gettext id, shown to the user."""

    def __init__(self, message: str, **params: Any) -> None:
        super().__init__(message)
        self.message = message
        self.params = params


class SearchTimeout(Exception):
    """Even the search without ranking exceeded the time limit."""


@dataclass(frozen=True)
class SearchRequest:
    query: str
    kinds: tuple[str, ...] = ()
    language: str | None = None
    owner_id: uuid.UUID | None = None
    since: datetime | None = None
    before: datetime | None = None
    include_archived: bool = False
    sort: str = "relevance"
    page: int = 1

    def filter_names(self) -> list[str]:
        """Names of the filters in use (for the log line: never their values)."""
        used = {
            "kind": bool(self.kinds),
            "language": self.language is not None,
            "author": self.owner_id is not None,
            "date": self.since is not None or self.before is not None,
            "archived": self.include_archived,
        }
        return [name for name, on in used.items() if on]


@dataclass(frozen=True)
class SearchHit:
    document_id: uuid.UUID
    title: str
    status: str
    kind: str
    filename: str
    owner_name: str
    doc_date: datetime
    score: float
    snippet: Markup | None = None
    origin: list[dict[str, Any]] = field(default_factory=list)
    page_no: int | None = None
    chunk_no: int = 1
    language: str | None = None


@dataclass(frozen=True)
class SearchPage:
    hits: list[SearchHit]
    total: int
    page: int
    pages: int
    size: int
    by_date_fallback: bool = False  # ranking took too long: results by date
    notice: str | None = None  # English gettext id of a hint (e.g. only very common words)


class SearchBackend(Protocol):
    def search(self, db: Session, user: CurrentUser, request: SearchRequest) -> SearchPage: ...
