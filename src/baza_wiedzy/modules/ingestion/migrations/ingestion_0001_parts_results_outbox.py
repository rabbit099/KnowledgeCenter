"""ingestion: ing_parts, ing_results, ing_outbox.

Revision ID: ingestion_0001
Revises:
Create Date: 2026-11-17

Phase 2: extracted text lives in ingestion (ADR-020); the outbox makes the
document.version.extracted event durable. Foreign keys point to doc_versions of the required
module documents (CLAUDE.md 6a); deleting a version removes its parts.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "ingestion_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("ingestion",)
depends_on: str | Sequence[str] | None = ("documents_0002",)

UUID = pg.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _version_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["version_id"],
        ["doc_versions.id"],
        ondelete="CASCADE",
        name=f"fk_{table}_version_id_doc_versions",
    )


def upgrade() -> None:
    op.create_table(
        "ing_parts",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("version_id", UUID, nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("part_type", sa.Text, nullable=False),
        sa.Column("origin", pg.JSONB, nullable=False),
        sa.Column("page_no", sa.Integer),
        sa.Column("chunk_no", sa.Integer, nullable=False),
        sa.Column("language", sa.Text, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("char_count", sa.Integer, nullable=False),
        sa.Column("needs_ocr", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "part_type IN ('page', 'body', 'attachment')", name="ck_ing_parts_part_type"
        ),
        sa.CheckConstraint("language IN ('pl', 'en', 'unknown')", name="ck_ing_parts_language"),
        _version_fk("ing_parts"),
        sa.PrimaryKeyConstraint("id", name="pk_ing_parts"),
        sa.UniqueConstraint("version_id", "seq", name="uq_ing_parts_version_id_seq"),
    )

    op.create_table(
        "ing_results",
        sa.Column("version_id", UUID, nullable=False),
        sa.Column("notes", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("parts_count", sa.Integer, nullable=False),
        sa.Column("chars_total", sa.BigInteger, nullable=False),
        sa.Column("finished_at", TS, nullable=False, server_default=sa.func.now()),
        _version_fk("ing_results"),
        sa.PrimaryKeyConstraint("version_id", name="pk_ing_results"),
    )

    op.create_table(
        "ing_outbox",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("event", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("delivered_at", TS),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text),
        sa.PrimaryKeyConstraint("id", name="pk_ing_outbox"),
    )
    op.create_index(
        "ix_ing_outbox_undelivered",
        "ing_outbox",
        ["id"],
        postgresql_where=sa.text("delivered_at IS NULL"),
    )
    op.create_index("ix_ing_outbox_delivered_at", "ing_outbox", ["delivered_at"])


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; restore a backup to go back.")
