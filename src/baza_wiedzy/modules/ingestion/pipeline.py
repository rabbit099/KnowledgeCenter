"""Processing of one version: claim -> scan hook -> extract -> chunk + language -> store.

Idempotent and retryable (CLAUDE.md 6):
- `claim` (documents API) moves pending -> processing under a row lock; a duplicate message or
  a version that is already done does nothing.
- The result is stored in ONE transaction: old parts of the version are deleted, new parts,
  notes, e-mail headers, the final status and the outbox entry are written together. Running
  the same version twice therefore never duplicates parts.
- Permanent errors (damaged file, unsupported encoding, limits) fail the version at once with
  a readable message; transient errors (database, Redis, time limit, external process) are
  retried with an exponential delay up to INGEST_MAX_ATTEMPTS.

Logs carry identifiers, kinds, error classes and counts only: never text, file names or
e-mail headers.
"""

from __future__ import annotations

import enum
import logging
import shutil
import tempfile
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import redis
from dramatiq.middleware.time_limit import TimeLimitExceeded
from sqlalchemy import delete
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.events import DOCUMENT_VERSION_EXTRACTED
from baza_wiedzy.core.runtime import Runtime
from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.modules.documents.services import api as documents
from baza_wiedzy.modules.ingestion import chunks, language, outbox, scanning
from baza_wiedzy.modules.ingestion.extractors.base import (
    Collector,
    Context,
    Limits,
    PermanentError,
    TransientError,
    find,
)
from baza_wiedzy.modules.ingestion.models import Part, Result

log = logging.getLogger(__name__)

WORKDIR = ".work"
NOT_YET = N_("This file type cannot be processed yet.")
INTERNAL = N_(
    "The file could not be processed because of an internal error. Details are in the log."
)
RETRYING = N_("Temporary problem; processing will be retried automatically.")
GAVE_UP = N_("Processing failed several times because of a temporary problem. Try again later.")
TIMEOUT = N_("Processing took longer than the time limit.")


class _Superseded(Exception):
    """The version was reprocessed or recovered meanwhile; this attempt's result is dropped."""


class Outcome(enum.StrEnum):
    SKIPPED = "skipped"
    DONE = "done"
    FAILED = "failed"
    RETRY = "retry"


@dataclass(frozen=True)
class StoredPart:
    part_type: str
    origin: list[dict[str, object]]
    page_no: int | None
    chunk_no: int
    language: str
    text: str
    needs_ocr: bool


TRANSIENT = (TransientError, OperationalError, DBAPIError, redis.RedisError, TimeLimitExceeded)


def _now() -> datetime:
    return datetime.now(UTC)


def stale_before(settings: Settings) -> datetime:
    return _now() - timedelta(minutes=settings.ingest_stale_minutes)


def limits_of(settings: Settings) -> Limits:
    return Limits(
        max_depth=settings.ingest_max_depth,
        max_parts=settings.ingest_max_parts,
        max_text_chars=settings.ingest_max_text_mb * 1024 * 1024,
    )


def retry_delay(settings: Settings, attempt: int) -> int:
    """Seconds before the next attempt: min * 2^(attempt-1), at most max."""
    delay = settings.ingest_retry_min_seconds * (2 ** max(0, attempt - 1))
    return int(min(delay, settings.ingest_retry_max_seconds))


def workdir_root(settings: Settings) -> Path:
    root = Path(settings.storage_dir) / WORKDIR
    root.mkdir(parents=True, exist_ok=True, mode=0o750)
    return root


def cleanup_workdirs(settings: Settings, *, older_than_seconds: int | None = None) -> int:
    """Remove temporary directories left by killed attempts (older than twice the time limit)."""
    root = Path(settings.storage_dir) / WORKDIR
    if not root.is_dir():
        return 0
    age = older_than_seconds
    if age is None:
        age = max(2 * settings.ingest_time_limit_seconds, 3600)
    limit = time.time() - age
    removed = 0
    for item in root.iterdir():
        try:
            if item.stat().st_mtime < limit:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed


def extract(settings: Settings, claim: documents.Claim, source: Path, workdir: Path) -> Collector:
    collector = Collector(limits=limits_of(settings), workdir=workdir)
    extractor = find(claim.kind)
    if extractor is None:
        raise PermanentError(NOT_YET, "kind_not_supported")
    extractor(source, Context(collector=collector))
    return collector


def build_parts(settings: Settings, collector: Collector) -> list[StoredPart]:
    stored: list[StoredPart] = []
    for raw in collector.parts:
        pieces = chunks.split(raw.text)
        for number, piece in enumerate(pieces, start=1):
            stored.append(
                StoredPart(
                    part_type=raw.part_type,
                    origin=raw.origin,
                    page_no=raw.page_no,
                    chunk_no=number,
                    language=language.detect(piece, min_letters=settings.language_min_letters),
                    text=piece,
                    needs_ocr=raw.needs_ocr,
                )
            )
    if len(stored) > settings.ingest_max_parts:
        raise PermanentError(N_("The file has more parts than the allowed limit."), "part_limit")
    return stored


def store(
    db: Session, claim: documents.Claim, collector: Collector, parts: list[StoredPart]
) -> bool:
    """Write the whole result; False (caller rolls back) when the attempt is no longer current."""
    needs_ocr = any(part.needs_ocr for part in parts)
    if not documents.finish(db, claim, needs_ocr=needs_ocr, page_count=collector.page_count):
        return False
    db.execute(delete(Part).where(Part.version_id == claim.version_id))
    db.execute(delete(Result).where(Result.version_id == claim.version_id))
    db.add_all(
        Part(
            version_id=claim.version_id,
            seq=seq,
            part_type=part.part_type,
            origin=part.origin,
            page_no=part.page_no,
            chunk_no=part.chunk_no,
            language=part.language,
            text=part.text,
            char_count=len(part.text),
            needs_ocr=part.needs_ocr,
        )
        for seq, part in enumerate(parts, start=1)
    )
    db.add(
        Result(
            version_id=claim.version_id,
            notes=collector.notes,
            parts_count=len(parts),
            chars_total=sum(len(part.text) for part in parts),
        )
    )
    email = collector.email
    if email is not None:
        documents.save_email_metadata(
            db,
            claim.version_id,
            subject=email.subject,
            from_addr=email.from_addr,
            to_addrs=email.to_addrs,
            cc_addrs=email.cc_addrs,
            sent_at=email.sent_at,
            message_id=email.message_id,
        )
    outbox.add(
        db,
        DOCUMENT_VERSION_EXTRACTED,
        {"document_id": str(claim.document_id), "version_id": str(claim.version_id)},
    )
    return True


def _safe_trace(exc: BaseException) -> str:
    """Traceback frames (file, line, function) without the exception message and without
    source lines: both could quote document content."""
    return "\n".join(
        f"  {frame.filename}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(exc.__traceback__)
    )


def _fail(rt: Runtime, claim: documents.Claim, message: str) -> None:
    with rt.session_factory.begin() as db:
        documents.fail(db, claim, message)


def _retry(rt: Runtime, claim: documents.Claim, message: str) -> Outcome:
    settings = rt.settings
    from baza_wiedzy.modules.ingestion.tasks import enqueue

    try:
        with rt.session_factory.begin() as db:
            if claim.attempt >= settings.ingest_max_attempts:
                documents.fail(db, claim, GAVE_UP if message == RETRYING else message)
                return Outcome.FAILED
            if not documents.retry_later(db, claim, RETRYING):
                return Outcome.SKIPPED
        enqueue(claim.version_id, settings, delay_seconds=retry_delay(settings, claim.attempt))
    except Exception as exc:  # database or Redis still down: stale recovery takes over
        log.warning(
            "ingestion: could not schedule a retry: version=%s error=%s",
            claim.version_id,
            type(exc).__name__,
        )
    return Outcome.RETRY


def process(rt: Runtime, version_id: uuid.UUID) -> Outcome:
    settings = rt.settings
    with rt.session_factory.begin() as db:
        claim = documents.claim(db, version_id, stale_before=stale_before(settings))
    if claim is None:
        log.info("ingestion: nothing to do for version=%s", version_id)
        return Outcome.SKIPPED
    log.info(
        "ingestion: start version=%s kind=%s attempt=%s", version_id, claim.kind, claim.attempt
    )
    started = time.monotonic()
    workdir = Path(tempfile.mkdtemp(prefix=f"{version_id.hex[:8]}-", dir=workdir_root(settings)))
    try:
        source = documents.original_path(settings.storage_dir, claim.storage_key)
        scanning.scan(settings, source)
        collector = extract(settings, claim, source, workdir)
        parts = build_parts(settings, collector)
        with rt.session_factory.begin() as db:
            if not store(db, claim, collector, parts):
                raise _Superseded()
    except _Superseded:
        log.info("ingestion: result discarded, attempt superseded: version=%s", version_id)
        return Outcome.SKIPPED
    except PermanentError as exc:
        log.info("ingestion: failed version=%s kind=%s reason=%s", version_id, claim.kind, exc.code)
        _fail(rt, claim, exc.message)
        return Outcome.FAILED
    except TimeLimitExceeded:
        log.warning("ingestion: time limit exceeded: version=%s", version_id)
        return _retry(rt, claim, TIMEOUT)
    except TRANSIENT as exc:
        log.warning(
            "ingestion: transient error version=%s error=%s attempt=%s",
            version_id,
            type(exc).__name__,
            claim.attempt,
        )
        return _retry(rt, claim, RETRYING)
    except Exception as exc:
        log.error(
            "ingestion: unexpected error version=%s kind=%s error=%s\n%s",
            version_id,
            claim.kind,
            type(exc).__name__,
            _safe_trace(exc),
        )
        _fail(rt, claim, INTERNAL)
        return Outcome.FAILED
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    log.info(
        "ingestion: done version=%s parts=%s chars=%s notes=%s seconds=%.2f",
        version_id,
        len(parts),
        sum(len(p.text) for p in parts),
        len(collector.notes),
        time.monotonic() - started,
    )
    try:
        outbox.deliver(rt.session_factory, rt.events, max_rows=100)
    except Exception as exc:  # stays in the outbox; the next run or the CLI delivers it
        log.warning("outbox: delivery postponed: error=%s", type(exc).__name__)
    return Outcome.DONE
