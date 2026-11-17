"""CSRF protection for every state-changing request (CLAUDE.md section 8).

Token = HMAC-SHA256(SECRET_KEY, binding). The binding is the session cookie (logged in) or a
random pre-session cookie (login form). The token travels in the `X-CSRF-Token` header (HTMX,
set once on <body> with hx-headers) or in the form field `csrf_token`. Requests whose Origin is
another site, or whose Sec-Fetch-Site says cross-site, are refused as well.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Awaitable, Callable, MutableMapping
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
HEADER = "x-csrf-token"
FORM_FIELD = "csrf_token"
MAX_FORM_BYTES = 64 * 1024  # larger bodies (uploads) must send the header


def cookie_names(secure: bool) -> tuple[str, str]:
    """(session cookie, pre-session cookie). The __Host- prefix requires Secure."""
    prefix = "__Host-" if secure else ""
    return f"{prefix}bw_session", f"{prefix}bw_pre"


def make_token(secret: str, binding: str) -> str:
    return hmac.new(secret.encode(), f"csrf:{binding}".encode(), hashlib.sha256).hexdigest()


def _cookies(scope: Scope) -> dict[str, str]:
    jar: SimpleCookie = SimpleCookie()
    for name, value in scope.get("headers", []):
        if name == b"cookie":
            try:
                jar.load(value.decode("latin-1"))
            except Exception:  # malformed cookie header: treat as no cookies
                return {}
    return {key: morsel.value for key, morsel in jar.items()}


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return str(value.decode("latin-1"))
    return None


def _same_origin(scope: Scope) -> bool:
    if (_header(scope, b"sec-fetch-site") or "same-origin") not in {"same-origin", "none"}:
        return False
    origin = _header(scope, b"origin")
    if origin is None or origin == "null":
        return origin is None
    host = _header(scope, b"host") or ""
    return origin in {f"{scope.get('scheme', 'https')}://{host}", f"https://{host}"}


class CSRFMiddleware:
    """Pure ASGI middleware: validates unsafe requests, exposes the token to templates."""

    def __init__(self, app: ASGIApp, *, secret: str, secure_cookies: bool) -> None:
        self.app = app
        self.secret = secret
        self.secure = secure_cookies
        self.session_cookie, self.pre_cookie = cookie_names(secure_cookies)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cookies = _cookies(scope)
        session_token = cookies.get(self.session_cookie)
        new_pre: str | None = None
        if session_token:
            binding = "s:" + hashlib.sha256(session_token.encode()).hexdigest()
        else:
            pre = cookies.get(self.pre_cookie)
            if not pre:
                pre = new_pre = secrets.token_urlsafe(24)
            binding = "p:" + pre
        expected = make_token(self.secret, binding)
        scope.setdefault("state", {})["csrf_token"] = expected

        if scope["method"] not in SAFE_METHODS:
            body, receive = await self._buffer(receive, scope)
            supplied = _header(scope, HEADER.encode()) or self._form_token(scope, body)
            if (
                new_pre is not None
                or not supplied
                or not hmac.compare_digest(supplied, expected)
                or not _same_origin(scope)
            ):
                await self._reject(send)
                return

        async def send_with_cookie(message: Message) -> None:
            if new_pre is not None and message["type"] == "http.response.start":
                attrs = "Path=/; HttpOnly; SameSite=Lax" + ("; Secure" if self.secure else "")
                cookie = f"{self.pre_cookie}={new_pre}; {attrs}"
                message.setdefault("headers", []).append((b"set-cookie", cookie.encode()))
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    @staticmethod
    async def _buffer(receive: Receive, scope: Scope) -> tuple[bytes, Receive]:
        content_type = (_header(scope, b"content-type") or "").split(";")[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            return b"", receive
        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > MAX_FORM_BYTES:
                break
            chunks.append(chunk)
            more = message.get("more_body", False)
        body = b"".join(chunks)
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return body, replay

    @staticmethod
    def _form_token(scope: Scope, body: bytes) -> str | None:
        if not body:
            return None
        values = parse_qs(body.decode("latin-1"), max_num_fields=100).get(FORM_FIELD)
        return values[0] if values else None

    @staticmethod
    async def _reject(send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": "Żądanie odrzucone (brak lub błędny token CSRF). Odśwież stronę.".encode(),
            }
        )
