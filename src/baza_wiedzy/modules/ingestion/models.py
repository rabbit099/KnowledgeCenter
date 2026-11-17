"""Tables of the ingestion module (prefix ing_). Created by ingestion_0001.

ing_parts holds the extracted text (ADR-020): one row per chunk of at most 100,000 characters
(tsvector limit, CLAUDE.md 7). search copies it into its own index through
ingestion.services.api. ing_results keeps per-version notes (detected encoding, skipped
attachments). ing_outbox makes document.version.extracted durable (ADR-020).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from baza_wiedzy.core.db.base import Base

PART_TYPES = ("page", "body", "attachment")
LANGUAGES = ("pl", "en", "unknown")


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Part(Base):
    __tablename__ = "ing_parts"
    __table_args__ = (
        CheckConstraint("part_type IN ('page', 'body', 'attachment')", name="part_type"),
        CheckConstraint("language IN ('pl', 'en', 'unknown')", name="language"),
        UniqueConstraint("version_id", "seq"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("doc_versions.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    part_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Path of origin, e.g. [{"type": "attachment", "name": "umowa.docx"}, {"type": "body"}].
    origin: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer)
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    needs_ocr: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql("false"))
    created_at: Mapped[datetime] = _now()


class Result(Base):
    __tablename__ = "ing_results"

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("doc_versions.id", ondelete="CASCADE"), primary_key=True
    )
    # [{"code": "encoding", "value": "windows-1250"}, {"code": "skipped", "origin": [...]}, ...]
    notes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=sql("'[]'::jsonb")
    )
    parts_count: Mapped[int] = mapped_column(Integer, nullable=False)
    chars_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finished_at: Mapped[datetime] = _now()


class OutboxEntry(Base):
    __tablename__ = "ing_outbox"
    __table_args__ = (
        Index(
            "ix_ing_outbox_undelivered",
            "id",
            postgresql_where=sql("delivered_at IS NULL"),
        ),
        Index("ix_ing_outbox_delivered_at", "delivered_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    # Identifiers only, never document text (ADR-020).
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _now()
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("0"))
    # Exception class of the last failed delivery (no message: it could carry content).
    last_error: Mapped[str | None] = mapped_column(Text)
