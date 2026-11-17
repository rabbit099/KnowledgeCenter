"""Process-wide services outside the web application: worker processes and CLI commands.

The web application keeps these on `app.state`. A Dramatiq actor has no request, so the worker
entry module (baza_wiedzy.worker) builds one `Runtime` per process and installs it; actors and
startup hooks of modules call `current()`. Nothing connects at build time (the engine and the
Redis client connect lazily), so building it in every forked worker process is cheap.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import redis
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.session import make_engine, make_session_factory
from baza_wiedzy.core.events import EventBus
from baza_wiedzy.core.registry import ModuleRegistry, load_ref

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    settings: Settings
    registry: ModuleRegistry
    engine: Engine
    session_factory: sessionmaker[Session]
    events: EventBus
    redis: redis.Redis

    def close(self) -> None:
        self.engine.dispose()
        self.redis.close()


_current: Runtime | None = None


def build(settings: Settings, registry: ModuleRegistry) -> Runtime:
    engine = make_engine(settings)
    events = EventBus()
    registry.register_events(events, settings)
    return Runtime(
        settings=settings,
        registry=registry,
        engine=engine,
        session_factory=make_session_factory(engine),
        events=events,
        redis=redis.Redis.from_url(settings.redis_url, socket_timeout=5, socket_connect_timeout=5),
    )


def install(runtime: Runtime | None) -> None:
    global _current
    _current = runtime


def current() -> Runtime:
    if _current is None:
        raise RuntimeError("No runtime installed (baza_wiedzy.worker or a CLI command does it).")
    return _current


def run_startup_hooks(runtime: Runtime) -> None:
    """Call the `worker_startup` hooks of the enabled modules; a failing hook is logged only.

    Hooks do recovery work (re-enqueue, redeliver, clean up). The worker must still start when
    one fails, for example while the database is briefly unavailable: the operator can run the
    same work later through the CLI.
    """
    for manifest in runtime.registry.modules:
        for ref in manifest.worker_startup:
            try:
                load_ref(ref)(runtime)
            except Exception:
                log.exception("worker startup hook %s failed", ref)
