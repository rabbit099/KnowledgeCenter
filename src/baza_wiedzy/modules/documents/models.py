"""Tables of the documents module (prefix doc_). Created by documents_0002.

Lifecycle columns (published_version_id, status values other than draft) exist from the start
(expand/contract); the lifecycle itself comes in Phase 5.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from baza_wiedzy.core.db.base import Base

KINDS = ("pdf", "docx", "doc", "txt", "eml", "msg")
STATUS_DRAFT, STATUS_PUBLISHED, STATUS_ARCHIVED = "draft", "published", "archived"
PENDING, PROCESSING, DONE, FAILED, NEEDS_OCR = (
    "pending",
    "processing",
    "done",
    "failed",
    "needs_ocr",
)
PROCESSING_STATUSES = (PENDING, PROCESSING, DONE, FAILED, NEEDS_OCR)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Document(Base):
    __tablename__ = "doc_documents"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="status"),
        CheckConstraint("title_source IN ('user', 'filename', 'subject')", name="title_source"),
        Index("ix_doc_documents_owner_id", "owner_id"),
        Index("ix_doc_documents_status", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # user (typed), filename (no title typed), subject (e-mail subject after processing)
    title_source: Mapped[str] = mapped_column(Text, nullable=False, server_default="user")
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=STATUS_DRAFT)
    latest_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("doc_versions.id", ondelete="SET NULL", use_alter=True)
    )
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("doc_versions.id", ondelete="SET NULL", use_alter=True)
    )
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Version(Base):
    __tablename__ = "doc_versions"
    __table_args__ = (
        CheckConstraint("kind IN ('pdf', 'docx', 'doc', 'txt', 'eml', 'msg')", name="kind"),
        CheckConstraint(
            "processing_status IN ('pending', 'processing', 'done', 'failed', 'needs_ocr')",
            name="processing_status",
        ),
        UniqueConstraint("document_id", "version_no"),
        Index("ix_doc_versions_sha256", "sha256"),
        Index("ix_doc_versions_processing_status", "processing_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("doc_documents.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    uploaded_at: Mapped[datetime] = _now()
    processing_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=PENDING)
    # Readable message for the user (English gettext id or a short text); details in the log.
    processing_error: Mapped[str | None] = mapped_column(Text)
    processing_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    page_count: Mapped[int | None] = mapped_column(Integer)
    ocr_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class EmailMetadata(Base):
    __tablename__ = "doc_email_metadata"

    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("doc_versions.id", ondelete="CASCADE"), primary_key=True
    )
    subject: Mapped[str | None] = mapped_column(Text)
    from_addr: Mapped[str | None] = mapped_column(Text)
    to_addrs: Mapped[str | None] = mapped_column(Text)
    cc_addrs: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_id: Mapped[str | None] = mapped_column(Text)
