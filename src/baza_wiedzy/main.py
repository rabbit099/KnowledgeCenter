"""ASGI application factory.

Run with `uvicorn --factory baza_wiedzy.main:create_app` (see docker/app/Dockerfile).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis
from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from baza_wiedzy import __version__
from baza_wiedzy.core import health
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.session import make_engine
from baza_wiedzy.core.events import EventBus
from baza_wiedzy.core.registry import load_ref, load_registry
from baza_wiedzy.core.web import routes as core_routes
from baza_wiedzy.core.web.templating import TEMPLATE_DIR, make_templates

STATIC_DIR = TEMPLATE_DIR.parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    # Fails with a readable error if ENABLED_MODULES is inconsistent (CLAUDE.md section 6a).
    registry = load_registry(settings.enabled_modules)
    engine = make_engine(settings)
    redis_client = redis.Redis.from_url(
        settings.redis_url, socket_timeout=2, socket_connect_timeout=2
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()
        redis_client.close()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.registry = registry
    app.state.engine = engine
    app.state.redis = redis_client
    app.state.events = EventBus()
    app.state.templates = make_templates(settings)
    app.state.ready_checks = {
        "database": health.database_check(engine),
        "redis": health.redis_check(redis_client),
    }
    registry.register_events(app.state.events)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(core_routes.router)
    for manifest in registry.modules:
        for ref in manifest.routers:
            router = load_ref(ref)
            if not isinstance(router, APIRouter):
                raise TypeError(f"{ref} is not a FastAPI APIRouter")
            app.include_router(router)
    return app
