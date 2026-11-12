"""A real Dramatiq worker on the test Redis: heartbeat, test job, /ready."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator

import dramatiq
import pytest
import redis
from fastapi.testclient import TestClient

from baza_wiedzy.cli import main
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.queue import HEARTBEAT_PREFIX, Heartbeat, setup_broker
from baza_wiedzy.core.registry import load_registry
from baza_wiedzy.core.tasks import PING_PREFIX, ping
from baza_wiedzy.main import create_app

pytestmark = pytest.mark.integration


def wait_for(condition: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def client(settings: Settings) -> Iterator[redis.Redis]:
    client = redis.Redis.from_url(settings.redis_url)
    yield client
    client.close()


def heartbeats(client: redis.Redis) -> list[bytes]:
    return list(client.scan_iter(match=f"{HEARTBEAT_PREFIX}*"))


@pytest.fixture
def worker(settings: Settings, client: redis.Redis) -> Iterator[dramatiq.Worker]:
    broker = setup_broker(settings, load_registry(settings.enabled_modules))
    broker.add_middleware(Heartbeat(client, interval=1))
    worker = dramatiq.Worker(broker, worker_threads=1, worker_timeout=100)
    worker.start()
    yield worker
    worker.stop()
    broker.close()


def test_worker_heartbeat_and_ready(
    settings: Settings, client: redis.Redis, worker: dramatiq.Worker
) -> None:
    assert wait_for(lambda: bool(heartbeats(client)))
    with TestClient(create_app(settings, setup_logging=False)) as http:
        response = http.get("/ready")
    assert response.status_code == 200, response.json()
    assert response.json()["checks"] == {
        "database": "ok",
        "redis": "ok",
        "schema": "ok",
        "worker": "ok",
    }


def test_ping_job_is_processed(client: redis.Redis, worker: dramatiq.Worker) -> None:
    token = uuid.uuid4().hex
    ping.send(token)
    assert wait_for(lambda: client.get(f"{PING_PREFIX}{token}") == b"pong")


def test_cli_worker_ping(
    settings: Settings, worker: dramatiq.Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REDIS_URL", settings.redis_url)
    assert main(["worker-ping", "--timeout", "10"]) == 0


def test_heartbeat_disappears_when_worker_stops(settings: Settings, client: redis.Redis) -> None:
    broker = setup_broker(settings, load_registry(settings.enabled_modules))
    broker.add_middleware(Heartbeat(client, interval=1))
    worker = dramatiq.Worker(broker, worker_threads=1, worker_timeout=100)
    worker.start()
    assert wait_for(lambda: bool(heartbeats(client)))
    worker.stop()
    broker.close()
    assert heartbeats(client) == []
    with TestClient(create_app(settings, setup_logging=False)) as http:
        response = http.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["worker"] == "no heartbeat"
