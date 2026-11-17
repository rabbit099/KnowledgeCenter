"""Transactional outbox for document.version.extracted (ADR-020, decision on open question 8).

`add` runs in the transaction that stores the extraction result, so the event exists exactly
when the result does. `deliver` publishes undelivered entries on the in-process event bus and
marks them delivered; rows are taken with FOR UPDATE SKIP LOCKED, so the worker start, the
worker after each version and `baza-wiedzy events redeliver` never deliver the same row at the
same time. A failing handler leaves its row undelivered (attempts + 1, exception class only)
for the next run. Handlers must be idempotent: delivery is at least once.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from baza_wiedzy.core.events import EventBus
from baza_wiedzy.modules.ingestion.models import OutboxEntry

log = logging.getLogger(__name__)
BATCH = 100


@dataclass(frozen=True)
class Delivery:
    delivered: int = 0
    failed: int = 0


def add(db: Session, event: str, payload: Mapping[str, Any]) -> None:
    db.add(OutboxEntry(event=event, payload=dict(payload)))


def deliver(
    factory: sessionmaker[Session], events: EventBus, *, max_rows: int = 10_000
) -> Delivery:
    delivered = failed = 0
    seen: set[int] = set()
    while delivered + failed < max_rows:
        with factory.begin() as db:
            query = (
                select(OutboxEntry)
                .where(OutboxEntry.delivered_at.is_(None))
                .order_by(OutboxEntry.id)
                .limit(BATCH)
                .with_for_update(skip_locked=True)
            )
            if seen:
                query = query.where(OutboxEntry.id.not_in(seen))
            rows = db.scalars(query).all()
            if not rows:
                break
            for row in rows:
                seen.add(row.id)
                try:
                    events.publish(row.event, row.payload)
                except Exception as exc:
                    row.attempts += 1
                    row.last_error = type(exc).__name__
                    failed += 1
                    log.warning(
                        "outbox: delivery failed: entry=%s event=%s error=%s",
                        row.id,
                        row.event,
                        type(exc).__name__,
                    )
                else:
                    row.delivered_at = datetime.now(UTC)
                    delivered += 1
    return Delivery(delivered=delivered, failed=failed)


def prune(factory: sessionmaker[Session], *, older_than_days: int) -> int:
    limit = datetime.now(UTC) - timedelta(days=older_than_days)
    with factory.begin() as db:
        result = db.execute(
            delete(OutboxEntry).where(
                OutboxEntry.delivered_at.is_not(None), OutboxEntry.delivered_at < limit
            )
        )
    return int(getattr(result, "rowcount", 0) or 0)


def undelivered(db: Session) -> int:
    from sqlalchemy import func

    return int(
        db.scalar(
            select(func.count()).select_from(OutboxEntry).where(OutboxEntry.delivered_at.is_(None))
        )
        or 0
    )
