"""Login and logout pages."""

from __future__ import annotations

import ipaddress
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from baza_wiedzy.core.audit.service import Actor
from baza_wiedzy.core.auth.deps import Db, optional_user, require_user
from baza_wiedzy.core.auth.service import Authenticator, Outcome
from baza_wiedzy.core.permissions.service import CurrentUser

router = APIRouter()


def safe_next(target: str | None) -> str:
    """Only local paths: no scheme, no host, no protocol-relative '//' (open redirect)."""
    if not target or not target.startswith("/") or target.startswith(("//", "/\\")):
        return "/"
    return target


def client_ip(request: Request) -> str | None:
    # Behind Caddy, uvicorn rewrites request.client from X-Forwarded-For, but only for requests
    # coming from FORWARDED_ALLOW_IPS (the proxy's fixed address, see docker-compose.yml).
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def _render(request: Request, status: int, *, error: str | None, next_url: str) -> Response:
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "login.html", {"error": error, "next_url": next_url}, status_code=status
    )


@router.get("/login", include_in_schema=False)
def login_page(
    request: Request,
    user: Annotated[CurrentUser | None, Depends(optional_user)],
    next: str | None = None,
) -> Response:
    if user is not None:
        return RedirectResponse(safe_next(next), status_code=303)
    return _render(request, 200, error=None, next_url=safe_next(next))


@router.post("/login", include_in_schema=False)
def login(
    request: Request,
    db: Db,
    username: Annotated[str, Form(max_length=256)] = "",
    password: Annotated[str, Form(max_length=1024)] = "",
    next: Annotated[str, Form(max_length=2048)] = "/",
) -> Response:
    app = request.app
    auth: Authenticator = app.state.authenticator
    result = auth.login(
        db,
        username,
        password,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        previous_token=request.cookies.get(app.state.session_cookie),
    )
    if result.outcome is Outcome.LOCKED:
        return _render(request, 429, error="locked", next_url=safe_next(next))
    if result.outcome is Outcome.AD_UNAVAILABLE:
        return _render(request, 503, error="ad_unavailable", next_url=safe_next(next))
    if result.outcome is not Outcome.OK or result.token is None:
        return _render(request, 401, error="failed", next_url=safe_next(next))
    response = RedirectResponse(safe_next(next), status_code=303)
    response.set_cookie(
        app.state.session_cookie,
        result.token,
        max_age=int(auth.limits.absolute.total_seconds()),
        path="/",
        secure=app.state.settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    # The pre-session cookie (CSRF binding before login) is not needed any more.
    response.delete_cookie(
        app.state.pre_cookie,
        path="/",
        secure=app.state.settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout", include_in_schema=False)
def logout(
    request: Request, db: Db, user: Annotated[CurrentUser, Depends(require_user)]
) -> Response:
    app = request.app
    token = request.cookies.get(app.state.session_cookie)
    if token:
        auth: Authenticator = app.state.authenticator
        auth.logout(db, token, Actor(user.id, user.username), client_ip(request))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(
        app.state.session_cookie,
        path="/",
        secure=app.state.settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response
