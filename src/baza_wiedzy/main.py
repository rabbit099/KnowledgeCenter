"""ASGI application factory.

Run with `uvicorn --factory baza_wiedzy.main:create_app` (see docker/app/Dockerfile).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import redis
from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from baza_wiedzy import __version__
from baza_wiedzy.core import health
from baza_wiedzy.core.auth import routes as auth_routes
from baza_wiedzy.core.auth.csrf import CSRFMiddleware, cookie_names
from baza_wiedzy.core.auth.ldap import AdDirectory, LdapConfig
from baza_wiedzy.core.auth.passwords import Passwords
from baza_wiedzy.core.auth.ratelimit import Limits, LoginLimiter
from baza_wiedzy.core.auth.service import Authenticator
from baza_wiedzy.core.auth.sessions import SessionLimits
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.migrate import schema_check
from baza_wiedzy.core.db.session import make_engine, make_session_factory
from baza_wiedzy.core.events import EventBus
from baza_wiedzy.core.logs import configure_logging
from baza_wiedzy.core.queue import heartbeat_check
from baza_wiedzy.core.registry import load_ref, load_registry
from baza_wiedzy.core.web import routes as core_routes
from baza_wiedzy.core.web.security import EXCEPTION_HANDLERS, SecurityHeadersMiddleware
from baza_wiedzy.core.web.templating import TEMPLATE_DIR, make_templates

log = logging.getLogger(__name__)
STATIC_DIR = TEMPLATE_DIR.parent / "static"
WEAK_SECRETS = frozenset({"change-me", "changeme", "secret"})


class ConfigurationError(RuntimeError):
    pass


def check_secret_key(secret: str) -> None:
    if len(secret) < 32 or secret.lower() in WEAK_SECRETS:
        raise ConfigurationError(
            "SECRET_KEY is missing or too weak (at least 32 characters). Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))" and put it in .env.'
        )


def create_app(settings: Settings | None = None, *, setup_logging: bool = True) -> FastAPI:
    settings = settings or Settings()
    if setup_logging:
        configure_logging(settings.log_level)
    check_secret_key(settings.secret_key)
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
        "schema": schema_check(engine, settings),
        "worker": heartbeat_check(redis_client),
    }
    registry.register_events(app.state.events)

    # Authentication and permissions (core).
    session_limits = SessionLimits(
        idle=timedelta(minutes=settings.session_idle_minutes),
        absolute=timedelta(hours=settings.session_absolute_hours),
    )
    app.state.session_factory = make_session_factory(engine)
    app.state.session_limits = session_limits
    app.state.session_cookie, app.state.pre_cookie = cookie_names(settings.session_cookie_secure)
    app.state.permission_catalog = frozenset(p.code for p in registry.permissions())
    app.state.authenticator = Authenticator(
        passwords=Passwords(
            max_concurrent=settings.argon2_max_concurrent,
            max_length=settings.local_password_max_length,
        ),
        limiter=LoginLimiter(
            redis_client,
            Limits(
                window_seconds=settings.login_window_minutes * 60,
                lockout_seconds=settings.login_lockout_minutes * 60,
                account=settings.login_max_failures_account,
                account_ip=settings.login_max_failures_account_ip,
                ip=settings.login_max_failures_ip,
            ),
        ),
        limits=session_limits,
        directory=(
            AdDirectory(LdapConfig.from_settings(settings)) if settings.ldap_enabled else None
        ),
    )
    if settings.ldap_enabled:
        log.info("Active Directory login enabled (%s)", settings.ldap_uri)
    else:
        log.info("Active Directory login disabled (LDAP_ENABLED=false): local accounts only")
    for exc_class, handler in EXCEPTION_HANDLERS.items():
        app.add_exception_handler(exc_class, handler)
    # Added last = outermost: security headers wrap everything, CSRF runs before the routes.
    app.add_middleware(
        CSRFMiddleware, secret=settings.secret_key, secure_cookies=settings.session_cookie_secure
    )
    app.add_middleware(SecurityHeadersMiddleware)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(auth_routes.router)
    app.include_router(core_routes.router)
    for manifest in registry.modules:
        for ref in manifest.routers:
            router = load_ref(ref)
            if not isinstance(router, APIRouter):
                raise TypeError(f"{ref} is not a FastAPI APIRouter")
            app.include_router(router)
    return app
