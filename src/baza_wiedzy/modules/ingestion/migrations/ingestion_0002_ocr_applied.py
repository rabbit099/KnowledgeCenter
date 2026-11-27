"""ingestion: ing_parts.ocr_applied (the text of the part was recognised by OCR).

Revision ID: ingestion_0002
Revises: ingestion_0001
Create Date: 2026-11-20

Phase 4, ADR-031. Existing rows get false (no OCR ran before this version). Expand-only: older
application versions ignore the column (server default).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ingestion_0002"
down_revision: str | Sequence[str] | None = "ingestion_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ing_parts",
        sa.Column("ocr_applied", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; restore a backup to go back.")
