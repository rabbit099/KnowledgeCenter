"""Entry module of the worker: `dramatiq baza_wiedzy.worker --processes N --threads 1`.

Importing this module configures JSON logging, the broker, the heartbeat, the actors of the
enabled modules (that is how Dramatiq discovers them) and the process runtime (database engine,
event bus) used by the actors. It runs in every worker process, after Dramatiq has pointed
stderr at its log pipe; the supervising main process keeps Dramatiq's own plain-text format for
its few start and stop lines. When the worker has started, the modules' startup hooks run once
per process (re-enqueue waiting versions, redeliver events, clean up temporary files).
"""

from __future__ import annotations

import sys

import redis

from baza_wiedzy.core import runtime
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.logs import configure_logging
from baza_wiedzy.core.queue import Heartbeat, StartupHooks, setup_broker
from baza_wiedzy.core.registry import load_registry

settings = Settings()
configure_logging(settings.log_level, stream=sys.stderr)
registry = load_registry(settings.enabled_modules)
broker = setup_broker(settings, registry)
broker.add_middleware(
    Heartbeat(redis.Redis.from_url(settings.redis_url), interval=settings.worker_heartbeat_seconds)
)
process_runtime = runtime.build(settings, registry)
runtime.install(process_runtime)
broker.add_middleware(StartupHooks(lambda: runtime.run_startup_hooks(process_runtime)))
