"""Background jobs: Dramatiq on Redis (CLAUDE.md section 3).

Actors are declared in modules (manifest `tasks`) and bound to the broker at startup by
`bind_tasks`, so the same actor objects work with the real broker and with test brokers.
Each worker process keeps a heartbeat key in Redis; /ready checks that one exists.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from typing import Any

import dramatiq
import redis
from dramatiq import Actor, Broker, Middleware
from dramatiq.brokers.redis import RedisBroker

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.health import CheckResult, ReadyCheck
from baza_wiedzy.core.registry import ModuleRegistry, load_ref

log = logging.getLogger(__name__)

HEARTBEAT_PREFIX = "bw:worker:heartbeat:"


def make_broker(settings: Settings) -> RedisBroker:
    # dramatiq does not annotate RedisBroker.__init__.
    return RedisBroker(url=settings.redis_url)  # type: ignore[no-untyped-call]


def bind_tasks(broker: Broker, registry: ModuleRegistry) -> list[Actor[Any, Any]]:
    """Attach the actors of all enabled modules to `broker` and declare their queues."""
    actors: list[Actor[Any, Any]] = []
    for manifest in registry.modules:
        for ref in manifest.tasks:
            actor = load_ref(ref)
            if not isinstance(actor, Actor):
                raise TypeError(f"{ref} is not a Dramatiq actor")
            actor.broker = broker
            broker.declare_actor(actor)
            actors.append(actor)
    return actors


def setup_broker(settings: Settings, registry: ModuleRegistry) -> RedisBroker:
    """Make the Redis broker global and bind the enabled modules' actors to it."""
    broker = make_broker(settings)
    dramatiq.set_broker(broker)
    bind_tasks(broker, registry)
    return broker


class Heartbeat(Middleware):
    """Writes `bw:worker:heartbeat:<host>:<pid>` every `interval` seconds while the worker runs."""

    def __init__(self, client: redis.Redis, interval: int) -> None:
        self.client = client
        self.interval = interval
        self.key = f"{HEARTBEAT_PREFIX}{socket.gethostname()}:{os.getpid()}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        self.client.set(self.key, "alive", ex=self.interval * 4)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.beat()
            except redis.RedisError:
                log.warning("worker heartbeat: Redis unavailable", exc_info=True)
            self._stop.wait(self.interval)

    def after_worker_boot(self, broker: Broker, worker: dramatiq.Worker) -> None:
        # The key includes the pid of the process that actually runs the worker.
        self.key = f"{HEARTBEAT_PREFIX}{socket.gethostname()}:{os.getpid()}"
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="heartbeat", daemon=True)
        self._thread.start()

    def before_worker_shutdown(self, broker: Broker, worker: dramatiq.Worker) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            self.client.delete(self.key)
        except redis.RedisError:
            log.warning("worker heartbeat: could not remove the key", exc_info=True)


def heartbeat_check(client: redis.Redis) -> ReadyCheck:
    def check() -> CheckResult:
        try:
            alive = next(iter(client.scan_iter(match=f"{HEARTBEAT_PREFIX}*", count=100)), None)
        except Exception:
            log.exception("readiness: worker heartbeat check failed")
            return CheckResult(False, "unknown")
        return CheckResult(True) if alive is not None else CheckResult(False, "no heartbeat")

    return check
