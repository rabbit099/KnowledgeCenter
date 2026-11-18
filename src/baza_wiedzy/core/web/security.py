"""Security headers on every response, and handlers for 401/403.

The Content-Security-Policy allows only same-origin scripts and styles: templates contain no
inline scripts or styles, and HTMX runs with allowEval=false and includeIndicatorStyles=false
(meta htmx-config in base.html).
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from baza_wiedzy.core.auth.csrf import ASGIApp, Message, Receive, Scope, Send
from baza_wiedzy.core.auth.deps import NotAuthenticated, PermissionDenied

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; "
    "form-action 'self'"
)
HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
]


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_static = str(scope.get("path", "")).startswith("/static/")

        async def add_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                # A response that sets one of these itself (e.g. a download with a stricter
                # policy) keeps its own value; no header is sent twice.
                present = {name.lower() for name, _ in headers}
                defaults = list(HEADERS)
                if not is_static:
                    # Pages may show personal data; browsers must not keep them.
                    defaults.append((b"cache-control", b"no-store"))
                headers.extend((n, v) for n, v in defaults if n not in present)
            await send(message)

        await self.app(scope, receive, add_headers)


def not_authenticated(request: Request, _: Exception) -> Response:
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    login_url = f"/login?next={quote(target, safe='/')}"
    if request.headers.get("hx-request") == "true":
        return Response(status_code=401, headers={"HX-Redirect": login_url})
    return RedirectResponse(login_url, status_code=303)


def permission_denied(request: Request, exc: Exception) -> Response:
    templates: Jinja2Templates = request.app.state.templates
    code = exc.code if isinstance(exc, PermissionDenied) else ""
    return templates.TemplateResponse(request, "403.html", {"code": code}, status_code=403)


EXCEPTION_HANDLERS = {NotAuthenticated: not_authenticated, PermissionDenied: permission_denied}
