"""Queue plumbing without Redis."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from dramatiq.brokers.stub import StubBroker

from baza_wiedzy.core.queue import (
    HEARTBEAT_PREFIX,
    Heartbeat,
    bind_tasks,
    heartbeat_check,
    own_heartbeat_alive,
)
from baza_wiedzy.core.registry import load_registry
from baza_wiedzy.core.tasks import ping


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, tuple[str, int | None]] = {}

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = (value, ex)

    def delete(self, key: str) -> None:
        self.data.pop(key, None)

    def scan_iter(self, match: str, count: int) -> Iterator[str]:
        prefix = match.rstrip("*")
        return iter([k for k in self.data if k.startswith(prefix)])

    def get(self, key: str) -> str | None:
        return self.data[key][0] if key in self.data else None


def test_core_ping_is_bound_to_the_given_broker() -> None:
    broker = StubBroker()
    actors = bind_tasks(broker, load_registry(["documents"]))
    assert ping in actors
    assert ping.broker is broker
    assert "default" in broker.get_declared_queues()


def test_heartbeat_key_has_a_ttl() -> None:
    client: Any = FakeRedis()
    heartbeat = Heartbeat(client, interval=15)
    heartbeat.beat()
    [(key, (value, ttl))] = client.data.items()
    assert key.startswith(HEARTBEAT_PREFIX)
    assert (value, ttl) == ("*", 60)


def test_heartbeat_check() -> None:
    client: Any = FakeRedis()
    check = heartbeat_check(client, "ingestion")
    assert check().detail == "no heartbeat"
    Heartbeat(client, interval=15).beat()
    assert check().ok


def _beat(client: Any, queues: set[str] | None, pid: int) -> None:
    heartbeat = Heartbeat(client, interval=15)
    worker: Any = SimpleNamespace(consumer_whitelist=queues)
    heartbeat.after_worker_boot(StubBroker(), worker)
    heartbeat.before_worker_shutdown(StubBroker(), worker)  # stops the thread, removes the key
    heartbeat.key = f"{HEARTBEAT_PREFIX}host:{pid}"
    heartbeat.beat()


def test_heartbeat_names_the_queues_and_ready_checks_each_queue() -> None:
    """worker (--queues ingestion) alive, ocr-worker (--queues ocr) down: /ready must say so."""
    client: Any = FakeRedis()
    worker_check, ocr_check = heartbeat_check(client, "ingestion"), heartbeat_check(client, "ocr")
    _beat(client, {"ingestion"}, 1)
    assert [v for v, _ in client.data.values()] == ["ingestion"]
    assert worker_check().ok
    assert ocr_check().detail == "no heartbeat"
    _beat(client, {"ocr"}, 2)
    assert ocr_check().ok


def test_heartbeat_of_an_older_worker_counts_for_every_queue() -> None:
    client: Any = FakeRedis()
    client.set(f"{HEARTBEAT_PREFIX}old:1", "alive", ex=60)  # 0.4.x during an update
    assert heartbeat_check(client, "ocr")().ok


def test_own_heartbeat_is_matched_by_host_name(monkeypatch: pytest.MonkeyPatch) -> None:
    client: Any = FakeRedis()
    monkeypatch.setattr("socket.gethostname", lambda: "c0ffee")
    client.set(f"{HEARTBEAT_PREFIX}other:7", "*", ex=60)
    assert not own_heartbeat_alive(client)
    client.set(f"{HEARTBEAT_PREFIX}c0ffee:7", "ocr", ex=60)
    assert own_heartbeat_alive(client)
