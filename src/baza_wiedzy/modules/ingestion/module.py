"""Manifest of the ingestion module: text extraction pipeline in the worker (ADR-020)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.events import DOCUMENT_VERSION_UPLOADED, EventBus
from baza_wiedzy.core.registry import ModuleManifest


def _on_register(bus: EventBus, settings: Settings) -> None:
    def enqueue_uploaded(payload: Mapping[str, Any]) -> None:
        import logging

        from baza_wiedzy.modules.ingestion.tasks import enqueue

        try:
            enqueue(payload["version_id"], settings)
        except Exception as exc:  # Redis down: the version stays pending; worker start recovers
            logging.getLogger(__name__).warning(
                "ingestion: enqueue after upload failed: version=%s error=%s",
                payload.get("version_id"),
                type(exc).__name__,
            )

    bus.subscribe(DOCUMENT_VERSION_UPLOADED, enqueue_uploaded)


manifest = ModuleManifest(
    name="ingestion",
    version="0.1.0",
    requires=("documents",),
    routers=("baza_wiedzy.modules.ingestion.routes:router",),
    templates="baza_wiedzy.modules.ingestion",
    migrations_branch="ingestion",
    migrations_path="baza_wiedzy.modules.ingestion.migrations",
    tasks=("baza_wiedzy.modules.ingestion.tasks:process_version",),
    worker_startup=("baza_wiedzy.modules.ingestion.startup:on_worker_start",),
    on_register=_on_register,
)
