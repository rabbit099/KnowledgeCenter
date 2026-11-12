"""Entry module of the worker: `dramatiq baza_wiedzy.worker --processes N --threads 1`.

Importing this module configures the broker, the heartbeat and the actors of the enabled
modules (that is how Dramatiq discovers them).
"""

from __future__ import annotations

import redis

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.queue import Heartbeat, setup_broker
from baza_wiedzy.core.registry import load_registry

settings = Settings()
registry = load_registry(settings.enabled_modules)
broker = setup_broker(settings, registry)
broker.add_middleware(
    Heartbeat(redis.Redis.from_url(settings.redis_url), interval=settings.worker_heartbeat_seconds)
)
