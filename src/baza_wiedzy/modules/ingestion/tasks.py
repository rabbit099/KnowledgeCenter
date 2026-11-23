"""Dramatiq actor of the ingestion module and the helper that enqueues it.

Imported by the web application too (to enqueue), so the module stays light: the pipeline and
its parser libraries are imported inside the actor, in the worker only.

OCR (Phase 4 stage B, ADR-033) has its own queue "ocr", consumed only by the ocr-worker service
(`--queues ocr`), so ordinary uploads never wait for OCR. An OCR message carries no version: it
starts "the next queued version", the one with the fewest scanned pages (the order lives in
the database; Dramatiq's message priority only reorders messages a worker already prefetched).

Retries are not Dramatiq's: the pipeline counts attempts in doc_versions.processing_attempts and
re-enqueues with a delay only for transient errors (max_retries=0 here), so the database is the
single source of truth for the state of a version.
"""

from __future__ import annotations

import logging
import uuid

import dramatiq

from baza_wiedzy.core.config import Settings

log = logging.getLogger(__name__)
QUEUE = "ingestion"
OCR_QUEUE = "ocr"


@dramatiq.actor(actor_name="ingestion.process_version", queue_name=QUEUE, max_retries=0)
def process_version(version_id: str) -> None:
    from baza_wiedzy.core import runtime
    from baza_wiedzy.modules.ingestion.pipeline import process

    try:
        process(runtime.current(), uuid.UUID(version_id))
    except Exception as exc:
        # Only the class: messages of database errors may quote values. The version stays
        # "processing" and is recovered as stale (worker start, `reprocess --stuck`).
        log.error("ingestion: attempt aborted: version=%s error=%s", version_id, type(exc).__name__)


def enqueue(version_id: uuid.UUID | str, settings: Settings, *, delay_seconds: int = 0) -> None:
    process_version.send_with_options(
        args=(str(version_id),),
        delay=delay_seconds * 1000 if delay_seconds > 0 else None,
        time_limit=settings.ingest_time_limit_seconds * 1000,
    )


@dramatiq.actor(actor_name="ingestion.ocr_next", queue_name=OCR_QUEUE, max_retries=0)
def ocr_next() -> None:
    from baza_wiedzy.core import runtime
    from baza_wiedzy.modules.ingestion.pipeline import process_ocr

    try:
        process_ocr(runtime.current())
    except Exception as exc:
        # The attempt stays "running"; the recovery at worker start marks it failed.
        log.error("ocr: attempt aborted: error=%s", type(exc).__name__)


def enqueue_ocr(settings: Settings, *, count: int = 1) -> None:
    """One message per queued version; each starts the next one (smallest first)."""
    for _ in range(count):
        ocr_next.send_with_options(args=(), time_limit=(settings.ocr_timeout_seconds + 300) * 1000)
