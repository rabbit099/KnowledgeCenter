"""Route inventory shared by the route-inventory unit test and the permission-matrix test."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Mount

from baza_wiedzy.core.auth.deps import ACCESS_MARKER

PUBLIC = {
    ("GET", "/health"),
    ("GET", "/ready"),
    ("GET", "/login"),
    ("POST", "/login"),
    ("GET", "/logout"),  # only redirects to /login; logging out is the POST (CSRF)
}
PUBLIC_MOUNTS = {"/static"}


def access_markers(dependant: Any) -> set[str]:
    found: set[str] = set()
    for dependency in dependant.dependencies:
        marker = getattr(dependency.call, ACCESS_MARKER, None)
        if marker:
            found.add(marker)
        found |= access_markers(dependency)
    return found


def flatten(routes: list[Any]) -> list[Any]:
    """APIRoutes and Mounts, also inside included routers.

    FastAPI wraps included routers (`_IncludedRouter`, an internal class whose
    `original_router` holds the routes). Any other route type fails instead of being skipped,
    so a FastAPI change can never make the inventory pass on an empty list.
    """
    flat: list[Any] = []
    for route in routes:
        if isinstance(route, APIRoute | Mount):
            flat.append(route)
        elif hasattr(route, "original_router"):
            context = route.include_context
            assert not getattr(context, "dependencies", None), "router-level deps not supported"
            flat.extend(flatten(list(route.original_router.routes)))
        else:
            raise AssertionError(f"unknown route type {type(route)!r}: update route_helpers")
    return flat


def protected_endpoints(app: FastAPI) -> list[tuple[str, str, set[str]]]:
    """(method, path, access markers) for every non-public route."""
    endpoints = []
    for route in flatten(list(app.routes)):
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or ()):
            if (method, route.path) not in PUBLIC:
                endpoints.append((method, route.path, access_markers(route.dependant)))
    return endpoints


def allowed(markers: set[str], permissions: frozenset[str]) -> bool:
    """Would a logged-in user with `permissions` pass these access dependencies?"""
    for marker in markers:
        if marker.startswith("permission:") and marker.split(":", 1)[1] not in permissions:
            return False
        if marker.startswith("any:") and not set(marker.split(":", 1)[1].split(",")) & permissions:
            return False
    return True
