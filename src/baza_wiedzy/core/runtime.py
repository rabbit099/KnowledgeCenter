"""Process-wide services outside the web application: worker processes and CLI commands.

The web application keeps these on `app.state`. A Dramatiq actor has no request, so the worker
entry module (baza_wiedzy.worker) builds one `Runtime` per process and installs it; actors and
startup hooks of modules call `current()`. Nothing connects at build time (the engine and the
Redis client connect lazily), so building it in every forked worker process is cheap.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import redis
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.session import make_engine, make_session_factory
from baza_wiedzy.core.events import EventBus
from baza_wiedzy.core.health import CheckResult
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


SCHEMA_POLL_SECONDS = 5.0
SCHEMA_LOG_SECONDS = 60.0


def wait_for_schema(
    check: Callable[[], CheckResult],
    *,
    stop: threading.Event,
    interval: float = SCHEMA_POLL_SECONDS,
    log_every: float = SCHEMA_LOG_SECONDS,
) -> bool:
    """Block until the database schema is at all script heads (the /ready "schema" check).

    The worker can start before `baza-wiedzy migrate` (dev: `make up` then `make migrate`;
    a fresh server: `docker compose up -d` then migrate). Startup hooks touch module tables,
    so they wait here instead of failing. Logs a warning at once and then every `log_every`
    seconds. Returns False when `stop` is set (worker shutting down).
    """
    last_log = -log_every
    while not stop.is_set():
        result = check()
        if result.ok:
            return True
        now = time.monotonic()
        if now - last_log >= log_every:
            log.warning(
                "worker startup: waiting for database migrations (schema: %s); "
                "run `baza-wiedzy migrate`",
                result.detail,
            )
            last_log = now
        stop.wait(interval)
    return False


def start_startup_hooks(runtime: Runtime, stop: threading.Event) -> threading.Thread:
    """Wait for the schema, then run the startup hooks, in a background thread.

    The worker keeps its heartbeat and consumes jobs meanwhile (a job that needs a missing
    table fails like before and is recovered by the hooks once the schema is there).
    """
    from baza_wiedzy.core.db.migrate import schema_check

    check = schema_check(runtime.engine, runtime.settings)

    def run() -> None:
        if wait_for_schema(check, stop=stop):
            run_startup_hooks(runtime)

    thread = threading.Thread(target=run, name="startup-hooks", daemon=True)
    thread.start()
    return thread
