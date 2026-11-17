"""FastAPI dependencies: database session, current user, permission checks.

Enforcement is always on the server: `require_permission(...)` on the route. Templates only
hide what the user cannot do (`has_perm`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from baza_wiedzy.core.auth import sessions
from baza_wiedzy.core.permissions.service import CurrentUser, current_user


class NotAuthenticated(Exception):
    """No valid session: HTML requests are redirected to /login, HTMX gets HX-Redirect."""


class PermissionDenied(Exception):
    """Logged in, but missing a permission: 403."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def get_db(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as db:
        yield db


Db = Annotated[Session, Depends(get_db)]


def optional_user(request: Request, db: Db) -> CurrentUser | None:
    if hasattr(request.state, "user"):
        user: CurrentUser | None = request.state.user
        return user
    token = request.cookies.get(request.app.state.session_cookie)
    found = None
    if token:
        loaded = sessions.load(db, token, request.app.state.session_limits)
        db.commit()  # persists last_seen_at or the deletion of an expired session
        if loaded is not None:
            found = current_user(db, loaded[1], request.app.state.permission_catalog)
    request.state.user = found
    return found


# Every dependency that enforces access carries this attribute; the route inventory test
# (tests/unit/test_route_inventory.py) fails for any route that has none of them.
ACCESS_MARKER = "__bw_access__"


def require_user(user: Annotated[CurrentUser | None, Depends(optional_user)]) -> CurrentUser:
    if user is None:
        raise NotAuthenticated()
    return user


setattr(require_user, ACCESS_MARKER, "user")


def require_permission(code: str) -> Callable[..., CurrentUser]:
    def dependency(user: Annotated[CurrentUser, Depends(require_user)]) -> CurrentUser:
        if not user.can(code):
            raise PermissionDenied(code)
        return user

    dependency.__name__ = f"require_permission_{code.replace('.', '_')}"
    setattr(dependency, ACCESS_MARKER, f"permission:{code}")
    return dependency


def require_any_permission(*codes: str) -> Callable[..., CurrentUser]:
    def dependency(user: Annotated[CurrentUser, Depends(require_user)]) -> CurrentUser:
        if not any(user.can(code) for code in codes):
            raise PermissionDenied(codes[0])
        return user

    dependency.__name__ = "require_any_permission"
    setattr(dependency, ACCESS_MARKER, f"any:{','.join(codes)}")
    return dependency
