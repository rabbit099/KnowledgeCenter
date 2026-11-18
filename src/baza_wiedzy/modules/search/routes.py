"""The search page: a plain GET form (works without JavaScript; every parameter in the URL, so
results can be bookmarked and the back button works). Permission documents.read; what each user
finds is decided by the visibility rule in SQL (documents.services.api.search_scope).

The query never reaches a log or the audit (ADR-025); errors show a message, never the query.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from baza_wiedzy.core.auth.deps import Db, require_permission
from baza_wiedzy.core.permissions.service import CurrentUser
from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.modules.documents.services import api as documents
from baza_wiedzy.modules.search.backend import (
    LANGUAGES,
    SORTS,
    QueryProblem,
    SearchRequest,
    SearchTimeout,
)
from baza_wiedzy.modules.search.postgres import PostgresBackend

router = APIRouter()
Reader = Annotated[CurrentUser, Depends(require_permission("documents.read"))]
WARSAW = ZoneInfo("Europe/Warsaw")


def _day(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _utc_start(day: date | None) -> datetime | None:
    return datetime.combine(day, time.min, WARSAW) if day else None


def may_see_archived(user: CurrentUser) -> bool:
    """Who gets the "show archived" filter: owners who can archive, and documents.edit_any."""
    return user.can("documents.edit_any") or user.can("documents.publish")


@router.get("/search", include_in_schema=False)
def search_page(
    request: Request,
    db: Db,
    user: Reader,
    q: str = "",
    kind: Annotated[list[str] | None, Query()] = None,
    lang: str = "",
    author: str = "",
    since: Annotated[str, Query(alias="from")] = "",
    until: Annotated[str, Query(alias="to")] = "",
    archived: str = "",
    sort: str = "relevance",
    page: str = "1",
) -> Response:
    settings = request.app.state.settings
    kinds = tuple(k for k in (kind or []) if k in documents.KIND_GROUPS)
    language = lang if lang in LANGUAGES else None
    try:
        owner_id = uuid.UUID(author) if author else None
    except ValueError:
        owner_id = None
    day_from, day_to = _day(since), _day(until)
    show_archived = archived == "1" and may_see_archived(user)
    sort = sort if sort in SORTS else "relevance"
    try:
        page_no = max(1, int(page))
    except ValueError:
        page_no = 1
    filters: dict[str, Any] = {
        "q": q,
        "kind": list(kinds),
        "lang": language or "",
        "author": str(owner_id) if owner_id else "",
        "from": day_from.isoformat() if day_from else "",
        "to": day_to.isoformat() if day_to else "",
        "archived": "1" if show_archived else "",
        "sort": sort,
    }
    context: dict[str, Any] = {
        "filters": filters,
        "kind_groups": list(documents.KIND_GROUPS),
        "languages": LANGUAGES,
        "owners": documents.visible_owners(db, user),
        "may_see_archived": may_see_archived(user),
        "result": None,
        "problem": None,
        "max_query": settings.search_max_query_chars,
    }
    if q.strip():
        search = SearchRequest(
            query=q,
            kinds=kinds,
            language=language,
            owner_id=owner_id,
            since=_utc_start(day_from),
            before=_utc_start(day_to + timedelta(days=1)) if day_to else None,
            include_archived=show_archived,
            sort=sort,
            page=page_no,
        )
        try:
            context["result"] = PostgresBackend(settings).search(db, user, search)
        except QueryProblem as exc:
            context["problem"] = (exc.message, exc.params)
        except SearchTimeout:
            context["problem"] = (
                N_("The search took too long. Make the query more specific or use a filter."),
                {},
            )

    def page_url(number: int) -> str:
        pairs = [(k, v) for k, v in filters.items() if v and k != "kind"]
        pairs += [("kind", k) for k in filters["kind"]]
        return "/search?" + urlencode([*pairs, ("page", str(number))])

    context["page_url"] = page_url
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(request, "search/search.html", context)
