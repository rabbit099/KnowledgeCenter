"""Tables of the search module (prefix srch_), created by search_0002 (ADR-026).

srch_parts: a copy of every text part of an extracted version (from ingestion.services.api)
with three generated tsvectors (ADR-003): pl (Hunspell), english, simple_unaccent. Each also
contains `ident_terms`, the normalised identifiers of the text (identifiers.py).
srch_documents: per version, the fields matched as metadata (title and e-mail subject with
weight A; file name, sender and recipients with weight B) in the same three configurations,
plus `meta_fold` (lower case, no diacritics) for pg_trgm near matches (typos).

Rows are per version, so Phase 5 can add versions without rebuilding anything: which version
is searched is decided by documents.services.api.search_scope. The tsvectors are generated
columns: after changing a dictionary or configuration, `baza-wiedzy reindex-search --all`
rewrites the rows so that they are computed again.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from baza_wiedzy.core.db.base import Base

CONFIGS = {"pl": "public.pl", "en": "pg_catalog.english", "simple": "public.simple_unaccent"}


def content_tsvector(config: str) -> str:
    return (
        f"to_tsvector('{config}'::regconfig, text_content) || "
        f"to_tsvector('{config}'::regconfig, ident_terms)"
    )


def meta_tsvector(config: str) -> str:
    c = f"'{config}'::regconfig"
    return (
        f"setweight(to_tsvector({c}, meta_a), 'A') || setweight(to_tsvector({c}, meta_b), 'B')"
        f" || setweight(to_tsvector({c}, ident_terms), 'B')"
    )


class SearchPart(Base):
    __tablename__ = "srch_parts"
    __table_args__ = (
        UniqueConstraint("version_id", "seq", name="uq_srch_parts_version_id_seq"),
        Index("ix_srch_parts_document_id", "document_id"),
        Index("ix_srch_parts_tsv_pl", "tsv_pl", postgresql_using="gin"),
        Index("ix_srch_parts_tsv_en", "tsv_en", postgresql_using="gin"),
        Index("ix_srch_parts_tsv_simple", "tsv_simple", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("doc_versions.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("doc_documents.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    part_type: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer)
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    # The text was recognised by OCR (search_0003): ranking x 0.9, label in the results.
    ocr_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    ident_terms: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tsv_pl: Mapped[Any] = mapped_column(TSVECTOR, Computed(content_tsvector(CONFIGS["pl"])))
    tsv_en: Mapped[Any] = mapped_column(TSVECTOR, Computed(content_tsvector(CONFIGS["en"])))
    tsv_simple: Mapped[Any] = mapped_column(TSVECTOR, Computed(content_tsvector(CONFIGS["simple"])))


class SearchDocument(Base):
    __tablename__ = "srch_documents"
    __table_args__ = (
        Index("ix_srch_documents_document_id", "document_id"),
        Index("ix_srch_documents_tsv_pl", "tsv_meta_pl", postgresql_using="gin"),
        Index("ix_srch_documents_tsv_en", "tsv_meta_en", postgresql_using="gin"),
        Index("ix_srch_documents_tsv_simple", "tsv_meta_simple", postgresql_using="gin"),
        Index(
            "ix_srch_documents_meta_fold_trgm",
            "meta_fold",
            postgresql_using="gin",
            postgresql_ops={"meta_fold": "gin_trgm_ops"},
        ),
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("doc_versions.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("doc_documents.id", ondelete="CASCADE"), nullable=False
    )
    meta_a: Mapped[str] = mapped_column(Text, nullable=False)  # title, e-mail subject
    meta_b: Mapped[str] = mapped_column(Text, nullable=False)  # file name, sender, recipients
    ident_terms: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    meta_fold: Mapped[str] = mapped_column(Text, nullable=False)
    tsv_meta_pl: Mapped[Any] = mapped_column(TSVECTOR, Computed(meta_tsvector(CONFIGS["pl"])))
    tsv_meta_en: Mapped[Any] = mapped_column(TSVECTOR, Computed(meta_tsvector(CONFIGS["en"])))
    tsv_meta_simple: Mapped[Any] = mapped_column(
        TSVECTOR, Computed(meta_tsvector(CONFIGS["simple"]))
    )
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
