"""documents: lifecycle columns (publish, unpublish, archive, restore; Phase 3).

Revision ID: documents_0004
Revises: documents_0003
Create Date: 2026-11-18

published_at / published_by: when and by whom the document was last published (kept while it
is archived, cleared by unpublish and restore); archived_at: when it was archived. A published
document must name its published version (check constraint; no existing row is published:
earlier versions had no way to publish). Expand-only: older application versions never write
these columns.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "documents_0004"
down_revision: str | Sequence[str] | None = "documents_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("doc_documents", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.add_column("doc_documents", sa.Column("published_by", UUID(as_uuid=True)))
    op.add_column("doc_documents", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.create_foreign_key(
        "fk_doc_documents_published_by_users",
        "doc_documents",
        "users",
        ["published_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_doc_documents_published_version",
        "doc_documents",
        "status <> 'published' OR published_version_id IS NOT NULL",
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; restore a backup to go back.")
