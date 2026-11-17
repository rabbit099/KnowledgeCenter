"""Every route needs a login or a permission, except an explicit public list.

Walks all routes of the real application (core and enabled modules), including HTMX partials
(they are ordinary routes), and fails if a route has no access dependency. This protects routes
that future modules add as well.
"""

from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute
from starlette.routing import Mount

from baza_wiedzy.core.auth.deps import ACCESS_MARKER
from baza_wiedzy.core.config import Settings
from baza_wiedzy.main import create_app

PUBLIC = {
    ("GET", "/health"),
    ("GET", "/ready"),
    ("GET", "/login"),
    ("POST", "/login"),
}
PUBLIC_MOUNTS = {"/static"}


def _access(dependant: Any) -> set[str]:
    found: set[str] = set()
    for dependency in dependant.dependencies:
        marker = getattr(dependency.call, ACCESS_MARKER, None)
        if marker:
            found.add(marker)
        found |= _access(dependency)
    return found


def _flatten(routes: list[Any]) -> list[Any]:
    """APIRoutes and Mounts, also inside included routers.

    FastAPI wraps included routers (`_IncludedRouter`, an internal class whose
    `original_router` holds the routes). Any other route type fails the test instead of being
    skipped, so a FastAPI change can never make this inventory pass on an empty list.
    """
    flat: list[Any] = []
    for route in routes:
        if isinstance(route, APIRoute | Mount):
            flat.append(route)
        elif hasattr(route, "original_router"):
            context = route.include_context
            assert not getattr(context, "dependencies", None), "router-level deps not supported"
            flat.extend(_flatten(list(route.original_router.routes)))
        else:
            raise AssertionError(f"unknown route type {type(route)!r}: update this test")
    return flat


def _app_routes() -> list[Any]:
    settings = Settings(  # type: ignore[call-arg]  # pydantic-settings init kwarg
        _env_file=None,
        secret_key="inventory-test-secret-key-0123456789",
        enabled_modules=("documents", "ingestion", "search"),
    )
    return _flatten(list(create_app(settings, setup_logging=False).routes))


def test_inventory_sees_the_routes() -> None:
    paths = {r.path for r in _app_routes() if isinstance(r, APIRoute)}
    assert {"/health", "/login", "/admin/users", "/admin/roles/{role_id}"} <= paths
    assert len(paths) >= 20


def _unprotected(routes: list[Any]) -> list[str]:
    found = []
    for route in routes:
        if isinstance(route, Mount):
            assert route.path in PUBLIC_MOUNTS, f"unexpected mount {route.path}"
            continue
        for method in route.methods or ():
            if (method, route.path) not in PUBLIC and not _access(route.dependant):
                found.append(f"{method} {route.path}")
    return found


def test_every_route_requires_login_or_permission() -> None:
    unprotected = _unprotected(_app_routes())
    assert not unprotected, f"routes without require_user/require_permission: {unprotected}"


def test_a_new_unprotected_route_is_detected() -> None:
    from fastapi import APIRouter

    leaky = APIRouter(prefix="/module-x")

    @leaky.get("/data")
    def data() -> dict[str, str]:  # pragma: no cover - never called
        return {}

    settings = Settings(  # type: ignore[call-arg]  # pydantic-settings init kwarg
        _env_file=None, secret_key="inventory-test-secret-key-0123456789"
    )
    app = create_app(settings, setup_logging=False)
    app.include_router(leaky)
    assert _unprotected(_flatten(list(app.routes))) == ["GET /module-x/data"]


def test_admin_routes_require_a_permission_not_just_a_login() -> None:
    weak = []
    for route in _app_routes():
        if isinstance(route, APIRoute) and route.path.startswith("/admin"):
            markers = _access(route.dependant)
            if not any(m.startswith(("permission:", "any:")) for m in markers):
                weak.append(route.path)
    assert not weak, f"admin routes without a permission check: {weak}"


def test_public_list_is_not_stale() -> None:
    present = {
        (method, r.path)
        for r in _app_routes()
        if isinstance(r, APIRoute)
        for method in r.methods or ()
    }
    assert present >= PUBLIC
