"""Simple synchronous in-process event bus.

Modules talk to each other through public service APIs or through these events, never by
importing each other's internals. Handlers run in the publisher's process and thread, in
subscription order; an exception in a handler propagates to the publisher. Events are not
persisted: a handler that must not lose work (for example indexing) has to be idempotent and
re-runnable from stored data.

Event names and payload shapes are part of the public contract between modules, so they live
here, in the core.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any, TypedDict

# document.published: a version of a document became visible to readers.
DOCUMENT_PUBLISHED = "document.published"
# document.version.extracted: ingestion extracted text from a version; search indexes it.
DOCUMENT_VERSION_EXTRACTED = "document.version.extracted"


class DocumentPublishedPayload(TypedDict):
    document_id: str
    version_id: str


class ExtractedPart(TypedDict):
    part_type: str  # page | body | attachment
    name: str | None
    page_no: int | None
    chunk_no: int
    language: str  # pl | en | mixed | unknown
    text: str
    ocr_applied: bool


class VersionExtractedPayload(TypedDict):
    document_id: str
    version_id: str
    parts: list[ExtractedPart]
    metadata: Mapping[str, Any]


Handler = Callable[[Mapping[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event: str, handler: Handler) -> None:
        self._handlers[event].append(handler)

    def publish(self, event: str, payload: Mapping[str, Any]) -> None:
        for handler in list(self._handlers.get(event, ())):
            handler(payload)
