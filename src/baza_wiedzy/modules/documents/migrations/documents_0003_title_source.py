"""documents: doc_documents.title_source (where the title came from).

Revision ID: documents_0003
Revises: documents_0002
Create Date: 2026-11-17

user: typed in the "Tytuł" field; filename: the file name without extension (no title typed);
subject: the subject of an e-mail (EML/MSG), set by processing while the title still came
from the file name. Existing rows become "user", so no title written so far is ever replaced.
Expand-only: older application versions ignore the column (server default).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "documents_0003"
down_revision: str | Sequence[str] | None = "documents_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "doc_documents",
        sa.Column("title_source", sa.Text, nullable=False, server_default="user"),
    )
    op.create_check_constraint(
        "ck_doc_documents_title_source",
        "doc_documents",
        "title_source IN ('user', 'filename', 'subject')",
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; restore a backup to go back.")
