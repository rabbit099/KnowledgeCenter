"""Public interface of the documents module (the only part other modules may import).

Visibility (CLAUDE.md 6, ADR-013, ADR-025): every query that returns documents or versions
applies `visibility_clause(user)` in SQL, never a filter on results (rules in visibility.py:
the owner sees own documents in every state; drafts: documents.read_drafts; published:
documents.read; archived: documents.edit_any).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from baza_wiedzy.core.permissions.service import CurrentUser
from baza_wiedzy.core.web.pagination import Page
from baza_wiedzy.modules.documents.filenames import sanitize as sanitize_filename
from baza_wiedzy.modules.documents.filetypes import UnsupportedFile
from baza_wiedzy.modules.documents.filetypes import detect as detect_kind
from baza_wiedzy.modules.documents.models import (
    DONE,
    FAILED,
    NEEDS_OCR,
    PENDING,
    PROCESSING,
    STATUS_ARCHIVED,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    Document,
    EmailMetadata,
    Version,
)
from baza_wiedzy.modules.documents.processing import (
    Claim,
    ProcessingBusy,
    apply_subject_title,
    claim,
    fail,
    finish,
    ids_with_status,
    recover_stuck,
    reset_for_reprocess,
    retry_later,
    save_email_metadata,
)
from baza_wiedzy.modules.documents.storage import LocalStorage
from baza_wiedzy.modules.documents.visibility import visibility_clause

__all__ = [
    "DONE",
    "FAILED",
    "NEEDS_OCR",
    "PENDING",
    "PROCESSING",
    "STATUS_ARCHIVED",
    "STATUS_DRAFT",
    "STATUS_PUBLISHED",
    "Claim",
    "Document",
    "EmailMetadata",
    "ProcessingBusy",
    "UnsupportedFile",
    "Version",
    "apply_subject_title",
    "can_reprocess",
    "claim",
    "detect_kind",
    "email_metadata_of",
    "fail",
    "finish",
    "get_visible_document",
    "get_visible_version",
    "ids_with_status",
    "list_documents",
    "original_path",
    "recover_stuck",
    "reset_for_reprocess",
    "retry_later",
    "sanitize_filename",
    "save_email_metadata",
    "versions_of",
    "visibility_clause",
    "visible_duplicates",
]


def get_visible_document(db: Session, user: CurrentUser, document_id: uuid.UUID) -> Document | None:
    return db.scalar(select(Document).where(Document.id == document_id, visibility_clause(user)))


def versions_of(db: Session, document: Document) -> list[Version]:
    return list(
        db.scalars(
            select(Version)
            .where(Version.document_id == document.id)
            .order_by(Version.version_no.desc())
        )
    )


def list_documents(
    db: Session, user: CurrentUser, *, q: str = "", scope: str = "own", page: int = 1
) -> Page[Any]:
    """Rows of (Document, latest Version). scope: own (default) or all (every visible one)."""
    query = (
        select(Document, Version)
        .join(Version, Version.id == Document.latest_version_id)
        .where(visibility_clause(user))
        .order_by(Document.created_at.desc(), Document.id)
    )
    if scope != "all":
        query = query.where(Document.owner_id == user.id)
    if q:
        like = f"%{q.lower()}%"
        query = query.where(
            or_(
                func.lower(Document.title).like(like),
                func.lower(Version.original_filename).like(like),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    size = 25
    page = max(1, min(page, max(1, -(-total // size))))
    rows = [tuple(row) for row in db.execute(query.limit(size).offset((page - 1) * size))]
    return Page(items=rows, page=page, total=total, size=size)


def visible_duplicates(
    db: Session, user: CurrentUser, sha256: str, *, exclude_version_id: uuid.UUID | None = None
) -> list[Document]:
    """Other documents with a version of identical content that THIS user may see.

    Never looks at documents the user cannot see (for example other people's drafts), so the
    duplicate warning does not reveal their existence.
    """
    query = (
        select(Document)
        .join(Version, Version.document_id == Document.id)
        .where(Version.sha256 == sha256, visibility_clause(user))
        .distinct()
        .order_by(Document.title)
        .limit(10)
    )
    if exclude_version_id is not None:
        query = query.where(Version.id != exclude_version_id)
    return list(db.scalars(query))


def get_visible_version(
    db: Session, user: CurrentUser, version_id: uuid.UUID
) -> tuple[Document, Version] | None:
    """A version together with its document, only if the user may see the document."""
    row = db.execute(
        select(Document, Version)
        .join(Version, Version.document_id == Document.id)
        .where(Version.id == version_id, visibility_clause(user))
    ).first()
    return (row[0], row[1]) if row is not None else None


def can_reprocess(user: CurrentUser, document: Document) -> bool:
    """Owner with documents.upload for own documents; documents.edit_any for any document."""
    if user.can("documents.edit_any"):
        return True
    return document.owner_id == user.id and user.can("documents.upload")


def email_metadata_of(db: Session, version_id: uuid.UUID) -> EmailMetadata | None:
    return db.get(EmailMetadata, version_id)


def original_path(storage_dir: str | Path, storage_key: str) -> Path:
    """Read-only path of the original file of a version (the key is validated as a UUID)."""
    return LocalStorage(storage_dir).original_path(storage_key)
