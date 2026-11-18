"""documents: doc_documents, doc_versions, doc_email_metadata.

Revision ID: documents_0002
Revises: documents_0001
Create Date: 2026-11-17

Phase 2: uploads create a draft document with its first version. processing_status is owned
by this module and updated by ingestion through documents.services.api (CLAUDE.md 6a).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "documents_0002"
down_revision: str | Sequence[str] | None = "documents_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _uuid_pk() -> sa.Column[Any]:
    return sa.Column("id", UUID, server_default=sa.text("gen_random_uuid()"), nullable=False)


def _now(name: str) -> sa.Column[Any]:
    return sa.Column(name, TS, nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "doc_documents",
        _uuid_pk(),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("latest_version_id", UUID),
        sa.Column("published_version_id", UUID),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')", name="ck_doc_documents_status"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], ondelete="RESTRICT", name="fk_doc_documents_owner_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_doc_documents"),
    )
    op.create_index("ix_doc_documents_owner_id", "doc_documents", ["owner_id"])
    op.create_index("ix_doc_documents_status", "doc_documents", ["status"])

    op.create_table(
        "doc_versions",
        _uuid_pk(),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("original_filename", sa.Text, nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column("uploaded_by", UUID, nullable=False),
        _now("uploaded_at"),
        sa.Column("processing_status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("processing_error", sa.Text),
        sa.Column("processing_attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("processing_started_at", TS),
        sa.Column("processed_at", TS),
        sa.Column("page_count", sa.Integer),
        sa.Column("ocr_applied", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.CheckConstraint(
            "kind IN ('pdf', 'docx', 'doc', 'txt', 'eml', 'msg')", name="ck_doc_versions_kind"
        ),
        sa.CheckConstraint(
            "processing_status IN ('pending', 'processing', 'done', 'failed', 'needs_ocr')",
            name="ck_doc_versions_processing_status",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["doc_documents.id"],
            ondelete="CASCADE",
            name="fk_doc_versions_document_id_doc_documents",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_doc_versions_uploaded_by_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_doc_versions"),
        sa.UniqueConstraint(
            "document_id", "version_no", name="uq_doc_versions_document_id_version_no"
        ),
        sa.UniqueConstraint("storage_key", name="uq_doc_versions_storage_key"),
    )
    op.create_index("ix_doc_versions_sha256", "doc_versions", ["sha256"])
    op.create_index("ix_doc_versions_processing_status", "doc_versions", ["processing_status"])

    op.create_foreign_key(
        "fk_doc_documents_latest_version_id_doc_versions",
        "doc_documents",
        "doc_versions",
        ["latest_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_doc_documents_published_version_id_doc_versions",
        "doc_documents",
        "doc_versions",
        ["published_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "doc_email_metadata",
        sa.Column("version_id", UUID, nullable=False),
        sa.Column("subject", sa.Text),
        sa.Column("from_addr", sa.Text),
        sa.Column("to_addrs", sa.Text),
        sa.Column("cc_addrs", sa.Text),
        sa.Column("sent_at", TS),
        sa.Column("message_id", sa.Text),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["doc_versions.id"],
            ondelete="CASCADE",
            name="fk_doc_email_metadata_version_id_doc_versions",
        ),
        sa.PrimaryKeyConstraint("version_id", name="pk_doc_email_metadata"),
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; restore a backup to go back.")
