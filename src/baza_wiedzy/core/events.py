"""Simple synchronous in-process event bus.

Modules talk to each other through public service APIs or through these events, never by
importing each other's internals. Handlers run in the publisher's process and thread, in
subscription order; an exception in a handler propagates to the publisher.

The bus itself does not persist anything. Durability is the publisher's job (ADR-020):
`document.version.extracted` is written to the ingestion outbox (ing_outbox) in the same
transaction as the extraction result and delivered (again) until a delivery succeeds, so
handlers must be idempotent. Payloads are thin: identifiers only, never document text; a
handler reads what it needs through the owning module's services/api.py.

Event names and payload shapes are part of the public contract between modules, so they live
here, in the core.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any, TypedDict

# document.version.uploaded: documents stored a new version; ingestion enqueues processing.
# Published by the web application after the commit; if it is lost (Redis down), the version
# stays "pending" and the worker start re-enqueues it.
DOCUMENT_VERSION_UPLOADED = "document.version.uploaded"
# document.version.extracted: ingestion stored the parts of a version (outbox, at least once);
# search (Phase 3) reads them through ingestion.services.api and indexes them.
DOCUMENT_VERSION_EXTRACTED = "document.version.extracted"
# document.published: a version of a document became visible to readers.
DOCUMENT_PUBLISHED = "document.published"


class VersionUploadedPayload(TypedDict):
    document_id: str
    version_id: str


class DocumentPublishedPayload(TypedDict):
    document_id: str
    version_id: str


class VersionExtractedPayload(TypedDict):
    document_id: str
    version_id: str


Handler = Callable[[Mapping[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event: str, handler: Handler) -> None:
        self._handlers[event].append(handler)

    def publish(self, event: str, payload: Mapping[str, Any]) -> None:
        for handler in list(self._handlers.get(event, ())):
            handler(payload)
