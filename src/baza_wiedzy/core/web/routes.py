"""Pages of the core: start page and the status fragment loaded with HTMX."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from baza_wiedzy.core.health import run_checks

router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request) -> Response:
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(request, "home.html")


@router.get("/status", response_class=HTMLResponse, include_in_schema=False)
def status_fragment(request: Request) -> Response:
    ok, results = run_checks(request.app.state.ready_checks)
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(request, "_status.html", {"ok": ok, "results": results})
