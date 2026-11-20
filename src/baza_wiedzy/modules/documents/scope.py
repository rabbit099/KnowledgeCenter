"""What search may look at, provided by the owner of the documents (ADR-026).

`search_scope` is an SQL subquery with one row per document the user may see (visibility_clause)
and the version that is searched for it (`Document.current_version_id` in SQL), plus the
columns that search filters and shows. Search joins its index to this subquery, so visibility,
the choice of version and the filters stay in SQL and in this module. Processing that failed
takes the version out of search (decision of Phase 3, backlog): its text may be missing or old.

`index_metadata` gives search the fields it copies into its own index (title, file name,
e-mail headers) when a version is (re)indexed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Subquery, and_, case, func, select
from sqlalchemy.orm import Session

from baza_wiedzy.core.db.models import User
from baza_wiedzy.core.permissions.service import CurrentUser
from baza_wiedzy.modules.documents.models import (
    FAILED,
    STATUS_ARCHIVED,
    STATUS_DRAFT,
    Document,
    EmailMetadata,
    Version,
)
from baza_wiedzy.modules.documents.visibility import visibility_clause

KIND_GROUPS: dict[str, tuple[str, ...]] = {
    "pdf": ("pdf",),
    "word": ("doc", "docx"),
    "email": ("eml", "msg"),
    "text": ("txt",),
}


@dataclass(frozen=True)
class ScopeFilters:
    kinds: tuple[str, ...] = ()  # keys of KIND_GROUPS; empty: all
    owner_id: uuid.UUID | None = None
    since: datetime | None = None  # document date >= since (UTC)
    before: datetime | None = None  # document date < before (UTC)
    include_archived: bool = False


def current_version_column() -> ColumnElement[Any]:
    """Document.current_version_id() as an SQL expression on doc_documents."""
    return case(
        (
            and_(Document.status != STATUS_DRAFT, Document.published_version_id.is_not(None)),
            Document.published_version_id,
        ),
        else_=Document.latest_version_id,
    )


def search_scope(user: CurrentUser, filters: ScopeFilters) -> Subquery:
    """Columns: document_id, version_id, status, owner_id, owner_name, title, kind, filename,
    doc_date (e-mail: sent date, else upload time)."""
    doc_date = func.coalesce(EmailMetadata.sent_at, Version.uploaded_at)
    query = (
        select(
            Document.id.label("document_id"),
            Version.id.label("version_id"),
            Document.status.label("status"),
            Document.owner_id.label("owner_id"),
            func.coalesce(User.display_name, User.username).label("owner_name"),
            Document.title.label("title"),
            Version.kind.label("kind"),
            Version.original_filename.label("filename"),
            doc_date.label("doc_date"),
        )
        .join(Version, Version.id == current_version_column())
        .join(User, User.id == Document.owner_id)
        .outerjoin(EmailMetadata, EmailMetadata.version_id == Version.id)
        .where(visibility_clause(user), Version.processing_status != FAILED)
    )
    if not filters.include_archived:
        query = query.where(Document.status != STATUS_ARCHIVED)
    if filters.kinds:
        kinds = [k for group in filters.kinds for k in KIND_GROUPS.get(group, ())]
        query = query.where(Version.kind.in_(kinds))
    if filters.owner_id is not None:
        query = query.where(Document.owner_id == filters.owner_id)
    if filters.since is not None:
        query = query.where(doc_date >= filters.since)
    if filters.before is not None:
        query = query.where(doc_date < filters.before)
    return query.subquery("scope")


@dataclass(frozen=True)
class IndexMetadata:
    document_id: uuid.UUID
    title: str
    filename: str
    kind: str
    subject: str | None
    sender: str | None
    recipients: str | None


def index_metadata(db: Session, version_id: uuid.UUID) -> IndexMetadata | None:
    row = db.execute(
        select(Document, Version, EmailMetadata)
        .join(Version, Version.document_id == Document.id)
        .outerjoin(EmailMetadata, EmailMetadata.version_id == Version.id)
        .where(Version.id == version_id)
    ).first()
    if row is None:
        return None
    document, version, email = row
    recipients = ", ".join(v for v in (email.to_addrs, email.cc_addrs) if v) if email else None
    return IndexMetadata(
        document_id=document.id,
        title=document.title,
        filename=version.original_filename,
        kind=version.kind,
        subject=email.subject if email else None,
        sender=email.from_addr if email else None,
        recipients=recipients or None,
    )


def visible_owners(db: Session, user: CurrentUser) -> list[tuple[uuid.UUID, str]]:
    """Owners of documents this user can see (the "author" filter), sorted by name."""
    name = func.coalesce(User.display_name, User.username)
    rows = db.execute(
        select(User.id, name)
        .join(Document, Document.owner_id == User.id)
        .where(visibility_clause(user))
        .distinct()
        .order_by(name)
    )
    return [(row[0], str(row[1])) for row in rows]
