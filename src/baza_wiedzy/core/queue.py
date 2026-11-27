"""Background jobs: Dramatiq on Redis (CLAUDE.md section 3).

Actors are declared in modules (manifest `tasks`) and bound to the broker at startup by
`bind_tasks`, so the same actor objects work with the real broker and with test brokers.
Each worker process keeps a heartbeat key in Redis whose value names the queues it consumes
("*" = all); /ready checks per queue that one exists (worker: "ingestion", ocr-worker: "ocr"),
and the Docker healthcheck of a worker container checks its own key:
`python -m baza_wiedzy.core.queue` (exit 0 = alive).
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
from collections.abc import Callable
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
ALL_QUEUES = "*"
LEGACY_VALUE = "alive"  # heartbeat of a worker before 0.5.0: consumed every queue


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
        self.queues = ALL_QUEUES
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        self.client.set(self.key, self.queues, ex=self.interval * 4)

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
        queues = worker.consumer_whitelist  # `--queues`; None = every queue
        self.queues = ",".join(sorted(queues)) if queues else ALL_QUEUES
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


class StartupHooks(Middleware):
    """Starts `start(stop_event)` once in each worker process, after the worker has booted.

    `start` returns a background thread (see core.runtime.start_startup_hooks); the stop event
    ends a wait for migrations when the worker shuts down.
    """

    def __init__(self, start: Callable[[threading.Event], threading.Thread]) -> None:
        self.start = start
        self._stop = threading.Event()

    def after_worker_boot(self, broker: Broker, worker: dramatiq.Worker) -> None:
        self._stop.clear()
        try:
            self.start(self._stop)
        except Exception:
            log.exception("worker startup hooks could not be started")

    def before_worker_shutdown(self, broker: Broker, worker: dramatiq.Worker) -> None:
        self._stop.set()


def _consumes(value: bytes | str | None, queue: str) -> bool:
    if value is None:  # expired between SCAN and GET
        return False
    text = value.decode() if isinstance(value, bytes) else value
    return text in (ALL_QUEUES, LEGACY_VALUE) or queue in text.split(",")


def heartbeat_check(client: redis.Redis, queue: str) -> ReadyCheck:
    """OK when a live worker process consumes `queue`."""

    def check() -> CheckResult:
        try:
            for key in client.scan_iter(match=f"{HEARTBEAT_PREFIX}*", count=100):
                if _consumes(client.get(key), queue):
                    return CheckResult(True)
        except Exception:
            log.exception("readiness: worker heartbeat check failed")
            return CheckResult(False, "unknown")
        return CheckResult(False, "no heartbeat")

    return check


def own_heartbeat_alive(client: redis.Redis) -> bool:
    """A heartbeat of a worker process in this container (key with this host name) exists."""
    match = f"{HEARTBEAT_PREFIX}{socket.gethostname()}:*"
    return next(iter(client.scan_iter(match=match, count=100)), None) is not None


def main() -> int:
    """Docker healthcheck of `worker` and `ocr-worker`. Reads only REDIS_URL; prints nothing on
    success, one short line on failure (shown by `docker inspect`)."""
    try:
        alive = own_heartbeat_alive(redis.Redis.from_url(os.environ["REDIS_URL"], socket_timeout=5))
    except Exception as exc:  # the class only: no URL (it holds the password)
        print(f"heartbeat check failed: {type(exc).__name__}")
        return 1
    if not alive:
        print("no heartbeat of a worker process in this container")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
