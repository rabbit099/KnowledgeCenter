"""Background tasks of the core."""

from __future__ import annotations

from typing import cast

import dramatiq
from dramatiq.brokers.redis import RedisBroker

PING_PREFIX = "bw:ping:"


@dramatiq.actor(actor_name="core.ping", queue_name="default", max_retries=0, time_limit=10_000)
def ping(token: str) -> None:
    """Answer `baza-wiedzy worker-ping`: proves that a worker takes jobs from the queue."""
    broker = cast(RedisBroker, ping.broker)
    broker.client.set(f"{PING_PREFIX}{token}", "pong", ex=60)
