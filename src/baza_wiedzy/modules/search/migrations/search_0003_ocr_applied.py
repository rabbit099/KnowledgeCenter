"""search: srch_parts.ocr_applied (Phase 4 stage C, ADR-034).

Revision ID: search_0003
Revises: search_0002
Create Date: 2026-11-23

Copy of ing_parts.ocr_applied (ingestion_0002): the part's text was recognised by OCR. Ranking
multiplies the score of such a part by 0.9, and results label it "tekst z OCR". Existing rows
get false: before 0.5.0 no part came from OCR, so no reindex is needed after the upgrade (a
development database with OCR data from Phase 4 test runs: `baza-wiedzy reindex-search --all`).
Expand-only; older application versions ignore the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "search_0003"
down_revision: str | Sequence[str] | None = "search_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = ("ingestion_0002",)


def upgrade() -> None:
    op.add_column(
        "srch_parts",
        sa.Column("ocr_applied", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; restore a backup to go back.")
