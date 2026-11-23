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

OCR of the scanned pages (doc_versions.ocr_status, Phase 4 stage B, ADR-033), only while the
version is needs_ocr:

    finish with scanned pages --> queued (<= OCR_AUTO_MAX_PAGES) | on_hold (more)
                                  | skipped (> OCR_MAX_PAGES) | none (OCR off)
    none | on_hold | failed --request_ocr ("Uruchom OCR", CLI)--> queued
    skipped --request_ocr(override) (CLI --ignore-page-limit)--> queued, ignoring OCR_MAX_PAGES
    queued --claim_ocr (smallest ocr_pages first)--> running
    running --finish_ocr--> done (processing done) | skipped | failed (fail_ocr)
    queued | running --cancel_ocr--> on_hold     (a running OCR is stopped by the worker)
    running older than the stale limit --recover_ocr--> failed
    any --reset_for_reprocess--> none            (the next pass decides again)

`finish_ocr` / `fail_ocr` apply only while the version is still running the same OCR attempt
(ocr_started_at), so a cancelled or reprocessed version is never overwritten.
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
    OCR_DONE,
    OCR_FAILED,
    OCR_NONE,
    OCR_ON_HOLD,
    OCR_QUEUED,
    OCR_RUNNING,
    OCR_SKIPPED,
    PENDING,
    PROCESSING,
    Document,
    EmailMetadata,
    Version,
)

INTERRUPTED = N_("Processing was interrupted too many times. Use “Process again” to retry.")
OCR_INTERRUPTED = N_("OCR was interrupted (worker stopped or out of memory). Start OCR again.")
OCR_REQUESTABLE = (OCR_NONE, OCR_ON_HOLD, OCR_FAILED)


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


def finish(
    db: Session,
    claim: Claim,
    *,
    needs_ocr: bool,
    page_count: int | None,
    ocr_applied: bool = False,
    ocr_status: str = OCR_NONE,
    ocr_pages: int | None = None,
) -> bool:
    """End of the text pass. With scanned pages, ocr_status is the decision of ingestion
    (queued, on_hold, skipped or none) and ocr_pages their number."""
    version = _current_attempt(db, claim)
    if version is None:
        return False
    version.processing_status = NEEDS_OCR if needs_ocr else DONE
    version.ocr_applied = ocr_applied  # some text of this version comes from OCR (Phase 4)
    version.processing_error = None
    version.processed_at = _now()
    version.page_count = page_count
    version.ocr_status = ocr_status if needs_ocr else OCR_NONE
    version.ocr_pages = ocr_pages if needs_ocr else None
    version.ocr_error = None
    version.ocr_started_at = None
    version.ocr_requested_at = _now() if version.ocr_status == OCR_QUEUED else None
    return True


@dataclass(frozen=True)
class OcrClaim:
    """One OCR attempt of a version; `started_at` identifies the attempt."""

    version_id: uuid.UUID
    document_id: uuid.UUID
    kind: str
    storage_key: str
    pages: int | None
    started_at: datetime
    ignore_page_limit: bool = False


def request_ocr(db: Session, version_id: uuid.UUID, *, override: bool = False) -> bool:
    """none | on_hold | failed -> queued (the caller enqueues a message); with `override` also
    skipped, and the attempt ignores OCR_MAX_PAGES. False: not possible (no scanned pages
    waiting, already queued or running, done, or skipped without override)."""
    version = _locked(db, version_id)
    allowed = (*OCR_REQUESTABLE, OCR_SKIPPED) if override else OCR_REQUESTABLE
    if (
        version is None
        or version.processing_status != NEEDS_OCR
        or version.ocr_status not in allowed
    ):
        return False
    version.ocr_override = override
    version.ocr_status = OCR_QUEUED
    version.ocr_requested_at = _now()
    version.ocr_error = None
    return True


def claim_ocr(db: Session) -> OcrClaim | None:
    """The queued version with the fewest scanned pages (then the oldest request) -> running.

    The order comes from the database, not from the queue: every message starts "the next
    one", so short documents always go first (ADR-033). SKIP LOCKED: parallel OCR workers never
    take the same version."""
    version = db.scalar(
        select(Version)
        .where(Version.ocr_status == OCR_QUEUED, Version.processing_status == NEEDS_OCR)
        .order_by(Version.ocr_pages.asc().nulls_last(), Version.ocr_requested_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if version is None:
        return None
    started = _now()
    version.ocr_status = OCR_RUNNING
    version.ocr_started_at = started
    version.ocr_error = None
    db.flush()
    return OcrClaim(
        version_id=version.id,
        document_id=version.document_id,
        kind=version.kind,
        storage_key=version.storage_key,
        pages=version.ocr_pages,
        started_at=started,
        ignore_page_limit=version.ocr_override,
    )


def _current_ocr(db: Session, claim: OcrClaim) -> Version | None:
    version = _locked(db, claim.version_id)
    if (
        version is None
        or version.processing_status != NEEDS_OCR
        or version.ocr_status != OCR_RUNNING
        or version.ocr_started_at != claim.started_at
    ):
        return None
    return version


def ocr_still_running(db: Session, claim: OcrClaim) -> bool:
    """False once the OCR was cancelled, reprocessed or recovered: the worker stops."""
    version = db.get(Version, claim.version_id, populate_existing=True)
    return (
        version is not None
        and version.ocr_status == OCR_RUNNING
        and version.ocr_started_at == claim.started_at
    )


def finish_ocr(
    db: Session,
    claim: OcrClaim,
    *,
    needs_ocr: bool,
    page_count: int | None,
    ocr_applied: bool,
    ocr_status: str = OCR_DONE,
) -> bool:
    """Store the outcome of a finished OCR attempt (the caller writes the new parts)."""
    version = _current_ocr(db, claim)
    if version is None:
        return False
    version.processing_status = NEEDS_OCR if needs_ocr else DONE
    version.ocr_status = ocr_status
    version.ocr_applied = ocr_applied
    version.ocr_error = None
    version.ocr_override = False
    version.processed_at = _now()
    version.page_count = page_count
    return True


def fail_ocr(db: Session, claim: OcrClaim, message: str) -> bool:
    """OCR failed (time limit, crash): the text pages stay searchable, the scans "wymaga OCR"."""
    version = _current_ocr(db, claim)
    if version is None:
        return False
    version.ocr_status = OCR_FAILED
    version.ocr_error = message
    version.ocr_override = False
    return True


def cancel_ocr(db: Session, version_id: uuid.UUID) -> bool:
    """queued | running -> on_hold ("Uruchom OCR" starts it again)."""
    version = _locked(db, version_id)
    if version is None or version.ocr_status not in (OCR_QUEUED, OCR_RUNNING):
        return False
    version.ocr_status = OCR_ON_HOLD
    version.ocr_started_at = None
    version.ocr_override = False
    return True


def recover_ocr(db: Session, *, stale_before: datetime) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """At worker start: OCR attempts left running by a killed OCR worker -> failed (a document
    that took the worker down is not retried by itself); returns (failed, still queued)."""
    stuck = db.scalars(
        select(Version)
        .where(Version.ocr_status == OCR_RUNNING)
        .where((Version.ocr_started_at < stale_before) | Version.ocr_started_at.is_(None))
        .with_for_update(skip_locked=True)
    ).all()
    for version in stuck:
        version.ocr_status = OCR_FAILED
        version.ocr_error = OCR_INTERRUPTED
    queued = list(db.scalars(select(Version.id).where(Version.ocr_status == OCR_QUEUED)))
    return [version.id for version in stuck], queued


def ocr_candidates(db: Session, *, limit: int = 10_000) -> list[tuple[uuid.UUID, int | None]]:
    """Versions with scanned pages whose OCR was never run or waits for a decision ("none",
    "on_hold"), oldest first, with their number of scanned pages (None: not counted yet,
    versions processed before Phase 4 stage B)."""
    rows = db.execute(
        select(Version.id, Version.ocr_pages)
        .where(
            Version.processing_status == NEEDS_OCR,
            Version.ocr_status.in_((OCR_NONE, OCR_ON_HOLD)),
        )
        .order_by(Version.uploaded_at)
        .limit(limit)
    )
    return [(version_id, pages) for version_id, pages in rows]


def set_ocr_pages(db: Session, version_id: uuid.UUID, pages: int) -> None:
    version = db.get(Version, version_id)
    if version is not None and version.ocr_pages is None:
        version.ocr_pages = pages


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
    # The next text pass decides about OCR again; a running OCR attempt notices and stops.
    version.ocr_status = OCR_NONE
    version.ocr_error = None
    version.ocr_started_at = None
    version.ocr_override = False
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
    document.updated_at = _now()
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
