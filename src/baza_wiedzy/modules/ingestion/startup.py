"""Recovery work: at every worker start (manifest worker_startup) and from the CLI.

1. Remove temporary directories of killed attempts (every process; age-based, so a directory
   in use by another process is never touched).
2. Versions stuck in "processing" longer than INGEST_STALE_MINUTES: back to the queue, or
   failed after INGEST_MAX_ATTEMPTS.
3. Versions still "pending" (the enqueue after upload was lost, e.g. Redis was down):
   enqueued again. A version already queued is simply skipped by the second message.
4. Undelivered outbox entries are delivered (SKIP LOCKED), old delivered ones removed.

Steps 2-4 run in one worker process at a time (a Redis lock for 60 s), because all processes
start together.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from baza_wiedzy.core.runtime import Runtime
from baza_wiedzy.modules.documents.services import api as documents
from baza_wiedzy.modules.ingestion import outbox, pipeline
from baza_wiedzy.modules.ingestion.tasks import enqueue

log = logging.getLogger(__name__)
LOCK_KEY = "bw:ingestion:recovery"


@dataclass(frozen=True)
class Recovery:
    requeued_stuck: int
    failed_stuck: int
    requeued_pending: int


def recover(rt: Runtime) -> Recovery:
    settings = rt.settings
    with rt.session_factory.begin() as db:
        requeued, failed = documents.recover_stuck(
            db,
            stale_before=pipeline.stale_before(settings),
            max_attempts=settings.ingest_max_attempts,
        )
    with rt.session_factory() as db:
        pending = documents.ids_with_status(db, documents.PENDING)
    for version_id in dict.fromkeys([*requeued, *pending]):
        enqueue(version_id, settings)
    return Recovery(len(requeued), len(failed), len(set(pending) - set(requeued)))


def on_worker_start(rt: Runtime) -> None:
    removed = pipeline.cleanup_workdirs(rt.settings)
    if removed:
        log.info("ingestion: removed %s orphaned temporary director(ies)", removed)
    if not rt.redis.set(LOCK_KEY, "1", nx=True, ex=60):
        return
    result = recover(rt)
    delivery = outbox.deliver(rt.session_factory, rt.events)
    pruned = outbox.prune(rt.session_factory, older_than_days=rt.settings.outbox_retention_days)
    log.info(
        "ingestion: startup recovery: stuck requeued=%s stuck failed=%s pending requeued=%s "
        "events delivered=%s failed=%s pruned=%s",
        result.requeued_stuck,
        result.failed_stuck,
        result.requeued_pending,
        delivery.delivered,
        delivery.failed,
        pruned,
    )
