"""Public interface of the ingestion module (the only part other modules may import).

Phase 3 (search) reads extracted text here (ADR-020): `parts_of` for one version after a
document.version.extracted event, `extracted_version_ids` for a full rebuild of the index.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from baza_wiedzy.core.audit.service import Actor, record
from baza_wiedzy.core.config import Settings
from baza_wiedzy.modules.documents.services import api as documents
from baza_wiedzy.modules.ingestion.models import Part, Result

__all__ = [
    "PartView",
    "ReprocessBusy",
    "cancel_ocr",
    "count_scanned_pages",
    "extracted_version_ids",
    "notes_of",
    "parts_of",
    "request_ocr",
    "request_reprocess",
    "text_part_counts",
]

ReprocessBusy = documents.ProcessingBusy


@dataclass(frozen=True)
class PartView:
    seq: int
    part_type: str
    origin: list[dict[str, Any]]
    page_no: int | None
    chunk_no: int
    language: str
    text: str
    char_count: int
    needs_ocr: bool
    ocr_applied: bool = False


def parts_of(db: Session, version_id: uuid.UUID) -> list[PartView]:
    rows = db.scalars(select(Part).where(Part.version_id == version_id).order_by(Part.seq))
    return [
        PartView(
            seq=p.seq,
            part_type=p.part_type,
            origin=p.origin,
            page_no=p.page_no,
            chunk_no=p.chunk_no,
            language=p.language,
            text=p.text,
            char_count=p.char_count,
            needs_ocr=p.needs_ocr,
            ocr_applied=p.ocr_applied,
        )
        for p in rows
    ]


def notes_of(db: Session, version_id: uuid.UUID) -> list[dict[str, Any]]:
    result = db.get(Result, version_id)
    return list(result.notes) if result is not None else []


def extracted_version_ids(db: Session) -> list[uuid.UUID]:
    return list(db.scalars(select(Result.version_id).order_by(Result.finished_at)))


def text_part_counts(
    db: Session, version_ids: list[uuid.UUID] | None = None
) -> dict[uuid.UUID, int]:
    """Extracted versions -> number of parts with text (what search indexes; a page without
    any text, e.g. a scan waiting for OCR, is not counted). For search consistency checks."""
    has_text = func.length(func.btrim(Part.text)) > 0
    query = (
        select(Result.version_id, func.count(Part.id).filter(has_text))
        .outerjoin(Part, Part.version_id == Result.version_id)
        .group_by(Result.version_id)
    )
    if version_ids is not None:
        query = query.where(Result.version_id.in_(version_ids))
    return {row[0]: int(row[1]) for row in db.execute(query)}


def request_reprocess(
    db: Session,
    settings: Settings,
    version_id: uuid.UUID,
    *,
    actor: Actor,
    ip: str | None,
) -> bool:
    """Reset a version to pending and audit it (the caller commits, then calls `enqueue`).

    False: no such version. Raises ReprocessBusy while an attempt is running. Permission checks
    are the caller's (routes: documents.services.api.can_reprocess; CLI: operator).
    """
    from baza_wiedzy.modules.ingestion.pipeline import stale_before

    version = db.get(documents.Version, version_id)
    if version is None:
        return False
    if not documents.reset_for_reprocess(db, version_id, stale_before=stale_before(settings)):
        return False
    record(
        db,
        "document.reprocess",
        actor,
        entity_type="document",
        entity_id=version.document_id,
        ip=ip,
        details={"version_id": str(version_id), "sha256": version.sha256},
    )
    return True


def count_scanned_pages(db: Session, version_id: uuid.UUID) -> int:
    """Pages of a version still waiting for OCR (one part per page, attachments included)."""
    query = select(func.count()).where(
        Part.version_id == version_id, Part.needs_ocr.is_(True), Part.chunk_no == 1
    )
    return int(db.scalar(query) or 0)


def request_ocr(
    db: Session,
    version_id: uuid.UUID,
    *,
    actor: Actor,
    ip: str | None,
    source: str = "button",
) -> bool:
    """ "Uruchom OCR" / `baza-wiedzy ocr`: queue the scanned pages of a version and audit it as
    document.ocr_request (the caller commits, then calls tasks.enqueue_ocr). False: nothing to
    start (no scanned pages waiting, already queued or running, done, or skipped). Permission
    checks are the caller's (routes: documents.services.api.can_reprocess; CLI: operator)."""
    version = db.get(documents.Version, version_id)
    if version is None:
        return False
    if version.ocr_pages is None:  # versions processed before Phase 4 stage B
        documents.set_ocr_pages(db, version_id, count_scanned_pages(db, version_id))
    if not documents.request_ocr(db, version_id):
        return False
    record(
        db,
        "document.ocr_request",
        actor,
        entity_type="document",
        entity_id=version.document_id,
        ip=ip,
        details={"version_id": str(version_id), "pages": version.ocr_pages, "source": source},
    )
    return True


def cancel_ocr(db: Session, version_id: uuid.UUID, *, actor: Actor, ip: str | None) -> bool:
    """ "Anuluj OCR": queued or running -> on hold, audited as document.ocr_cancel. A running
    OCR process is killed by the OCR worker within seconds; its result is discarded."""
    version = db.get(documents.Version, version_id)
    if version is None or not documents.cancel_ocr(db, version_id):
        return False
    record(
        db,
        "document.ocr_cancel",
        actor,
        entity_type="document",
        entity_id=version.document_id,
        ip=ip,
        details={"version_id": str(version_id), "pages": version.ocr_pages},
    )
    return True
