"""Manifest of the search module: full-text index, queries, snippets (Phase 3, ADR-026/027)."""

from __future__ import annotations

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.events import DOCUMENT_VERSION_EXTRACTED, EventBus
from baza_wiedzy.core.registry import MenuItem, ModuleManifest
from baza_wiedzy.core.web.i18n import N_


def _on_register(bus: EventBus, settings: Settings) -> None:
    # Delivered by the ingestion outbox (worker, `baza-wiedzy events redeliver`): an exception
    # leaves the event undelivered, so it is delivered again later.
    from baza_wiedzy.modules.search.indexer import on_extracted

    bus.subscribe(DOCUMENT_VERSION_EXTRACTED, on_extracted)


manifest = ModuleManifest(
    name="search",
    version="0.1.0",
    requires=("documents", "ingestion"),
    menu=(MenuItem(label=N_("Search"), path="/search", permission="documents.read", order=10),),
    routers=("baza_wiedzy.modules.search.routes:router",),
    templates="baza_wiedzy.modules.search",
    migrations_branch="search",
    migrations_path="baza_wiedzy.modules.search.migrations",
    worker_startup=("baza_wiedzy.modules.search.indexer:on_worker_start",),
    on_register=_on_register,
)
