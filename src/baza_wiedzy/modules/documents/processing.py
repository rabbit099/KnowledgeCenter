"""Processing status of versions (owned by documents, driven by ingestion through the API).

State machine of doc_versions.processing_status:

    pending --claim--> processing --finish--> done | needs_ocr
                                  --fail----> failed            (permanent error)
                                  --retry---> pending           (transient error, delayed)
    processing older than the stale limit --recover--> pending, or failed after max attempts
    done | failed | needs_ocr | pending --reprocess--> pending  (attempts reset)

Every transition locks the row (SELECT ... FOR UPDATE). `finish`, `fail` and `retry` only apply
to the attempt that claimed the version (same attempt number, still processing), so a late
result of an interrupted attempt never overwrites a newer one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.modules.documents.models import (
    DONE,
    FAILED,
    NEEDS_OCR,
    PENDING,
    PROCESSING,
    Document,
    EmailMetadata,
    Version,
)

INTERRUPTED = N_("Processing was interrupted too many times. Use “Process again” to retry.")


class ProcessingBusy(Exception):
    """The version is being processed right now; it cannot be reprocessed."""


@dataclass(frozen=True)
class Claim:
    """What ingestion needs to process one attempt of a version."""

    version_id: uuid.UUID
    document_id: uuid.UUID
    kind: str
    storage_key: str
    original_filename: str
    attempt: int


def _now() -> datetime:
    return datetime.now(UTC)


def _locked(db: Session, version_id: uuid.UUID) -> Version | None:
    return db.scalar(select(Version).where(Version.id == version_id).with_for_update())


def _stale(version: Version, stale_before: datetime) -> bool:
    started = version.processing_started_at
    return started is None or started < stale_before


def claim(db: Session, version_id: uuid.UUID, *, stale_before: datetime) -> Claim | None:
    """Start an attempt: pending (or stale processing) -> processing. None: nothing to do."""
    version = _locked(db, version_id)
    if version is None:
        return None
    status = version.processing_status
    if not (status == PENDING or (status == PROCESSING and _stale(version, stale_before))):
        return None
    version.processing_status = PROCESSING
    version.processing_started_at = _now()
    version.processing_attempts += 1
    version.processing_error = None
    db.flush()
    return Claim(
        version_id=version.id,
        document_id=version.document_id,
        kind=version.kind,
        storage_key=version.storage_key,
        original_filename=version.original_filename,
        attempt=version.processing_attempts,
    )


def _current_attempt(db: Session, claim: Claim) -> Version | None:
    version = _locked(db, claim.version_id)
    if (
        version is None
        or version.processing_status != PROCESSING
        or version.processing_attempts != claim.attempt
    ):
        return None
    return version


def finish(db: Session, claim: Claim, *, needs_ocr: bool, page_count: int | None) -> bool:
    version = _current_attempt(db, claim)
    if version is None:
        return False
    version.processing_status = NEEDS_OCR if needs_ocr else DONE
    version.processing_error = None
    version.processed_at = _now()
    version.page_count = page_count
    return True


def fail(db: Session, claim: Claim, message: str) -> bool:
    """Permanent error: `message` is a gettext message id shown to the user (no content)."""
    version = _current_attempt(db, claim)
    if version is None:
        return False
    version.processing_status = FAILED
    version.processing_error = message
    version.processed_at = _now()
    return True


def retry_later(db: Session, claim: Claim, message: str) -> bool:
    """Transient error: back to pending; the caller enqueues it again with a delay."""
    version = _current_attempt(db, claim)
    if version is None:
        return False
    version.processing_status = PENDING
    version.processing_error = message
    return True


def reset_for_reprocess(db: Session, version_id: uuid.UUID, *, stale_before: datetime) -> bool:
    """Put a version back in the queue with a fresh attempt counter. False: no such version.

    Raises ProcessingBusy while an attempt that is not stale is running.
    """
    version = _locked(db, version_id)
    if version is None:
        return False
    if version.processing_status == PROCESSING and not _stale(version, stale_before):
        raise ProcessingBusy()
    version.processing_status = PENDING
    version.processing_attempts = 0
    version.processing_error = None
    version.processing_started_at = None
    return True


def recover_stuck(
    db: Session, *, stale_before: datetime, max_attempts: int
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Versions left in `processing` by a killed worker: back to pending (returned first, to be
    enqueued) or failed when they already used all attempts (returned second)."""
    stuck = db.scalars(
        select(Version)
        .where(Version.processing_status == PROCESSING)
        .where(
            (Version.processing_started_at < stale_before)
            | (Version.processing_started_at.is_(None))
        )
        .with_for_update(skip_locked=True)
    ).all()
    requeued: list[uuid.UUID] = []
    failed: list[uuid.UUID] = []
    for version in stuck:
        if version.processing_attempts >= max_attempts:
            version.processing_status = FAILED
            version.processing_error = INTERRUPTED
            version.processed_at = _now()
            failed.append(version.id)
        else:
            version.processing_status = PENDING
            requeued.append(version.id)
    return requeued, failed


def ids_with_status(db: Session, status: str, *, limit: int = 10_000) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(Version.id)
            .where(Version.processing_status == status)
            .order_by(Version.uploaded_at)
            .limit(limit)
        )
    )


def apply_subject_title(db: Session, version_id: uuid.UUID, subject: str | None) -> bool:
    """E-mails: use the subject as the title when no title was typed at upload.

    Only while the title still comes from the file name and only for the latest version; a
    typed title (title_source "user") is never replaced. Case and characters are kept;
    whitespace is collapsed and the length limited like a typed title.
    """
    from baza_wiedzy.modules.documents.upload import MAX_TITLE

    clean = " ".join((subject or "").split())[:MAX_TITLE]
    if not clean:
        return False
    version = db.get(Version, version_id)
    if version is None:
        return False
    document = db.scalar(
        select(Document).where(Document.id == version.document_id).with_for_update()
    )
    if (
        document is None
        or document.latest_version_id != version_id
        or document.title_source == "user"
    ):
        return False
    document.title = clean
    document.title_source = "subject"
    return True


def save_email_metadata(
    db: Session,
    version_id: uuid.UUID,
    *,
    subject: str | None,
    from_addr: str | None,
    to_addrs: str | None,
    cc_addrs: str | None,
    sent_at: datetime | None,
    message_id: str | None,
) -> None:
    """Insert or replace the e-mail headers of a version (idempotent)."""
    row = db.get(EmailMetadata, version_id)
    if row is None:
        row = EmailMetadata(version_id=version_id)
        db.add(row)
    row.subject = subject
    row.from_addr = from_addr
    row.to_addrs = to_addrs
    row.cc_addrs = cc_addrs
    row.sent_at = sent_at
    row.message_id = message_id
    db.flush()
