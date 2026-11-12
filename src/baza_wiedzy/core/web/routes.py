"""Pages of the core: start page and the status fragment loaded with HTMX."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from baza_wiedzy.core.auth.deps import require_user
from baza_wiedzy.core.health import run_checks
from baza_wiedzy.core.permissions.service import CurrentUser

router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request, user: Annotated[CurrentUser, Depends(require_user)]) -> Response:
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(request, "home.html")


@router.get("/status", response_class=HTMLResponse, include_in_schema=False)
def status_fragment(
    request: Request, user: Annotated[CurrentUser, Depends(require_user)]
) -> Response:
    ok, results = run_checks(request.app.state.ready_checks)
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(request, "_status.html", {"ok": ok, "results": results})
