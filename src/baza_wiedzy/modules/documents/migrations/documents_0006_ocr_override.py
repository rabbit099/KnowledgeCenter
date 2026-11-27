"""documents: doc_versions.ocr_override (Phase 4 stage C, ADR-033).

Revision ID: documents_0006
Revises: documents_0005
Create Date: 2026-11-23

The next OCR attempt of the version ignores OCR_MAX_PAGES: set by `baza-wiedzy ocr <ID>
--ignore-page-limit` (audited, source cli_override) and cleared when the attempt ends. A separate
migration (not part of documents_0005) because databases of the development stack were already
migrated to documents_0005 in stage B. Expand-only: false for every existing row; older
application versions never read the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "documents_0006"
down_revision: str | Sequence[str] | None = "documents_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "doc_versions",
        sa.Column("ocr_override", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; restore a backup to go back.")
