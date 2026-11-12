"""Queue plumbing without Redis."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from dramatiq.brokers.stub import StubBroker

from baza_wiedzy.core.queue import HEARTBEAT_PREFIX, Heartbeat, bind_tasks, heartbeat_check
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
    assert (value, ttl) == ("alive", 60)


def test_heartbeat_check() -> None:
    client: Any = FakeRedis()
    check = heartbeat_check(client)
    assert check().detail == "no heartbeat"
    Heartbeat(client, interval=15).beat()
    assert check().ok
