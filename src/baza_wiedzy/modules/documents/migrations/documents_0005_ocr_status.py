"""documents: OCR state of a version (Phase 4, stage B, ADR-033).

Revision ID: documents_0005
Revises: documents_0004
Create Date: 2026-11-20

ocr_status: none | queued | running | done | failed | on_hold | skipped (see processing.py);
ocr_pages: scanned pages that need OCR (priority and the "large document" rule); ocr_requested_at
and ocr_started_at: queue order and stale detection; ocr_error: readable message id;
ocr_override: the next OCR attempt ignores OCR_MAX_PAGES (`baza-wiedzy ocr --ignore-page-limit`).
Expand-only: every existing row gets "none" (a version still "wymaga OCR" stays waiting;
`baza-wiedzy ocr --pending` starts those by hand), older application versions never read these
columns. Not released yet when ocr_override was added (Phase 4 stage C, before 0.5.0).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "documents_0005"
down_revision: str | Sequence[str] | None = "documents_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATES = "('none', 'queued', 'running', 'done', 'failed', 'on_hold', 'skipped')"


def upgrade() -> None:
    op.add_column(
        "doc_versions",
        sa.Column("ocr_status", sa.Text, nullable=False, server_default=sa.text("'none'")),
    )
    op.add_column("doc_versions", sa.Column("ocr_pages", sa.Integer))
    op.add_column("doc_versions", sa.Column("ocr_requested_at", sa.DateTime(timezone=True)))
    op.add_column("doc_versions", sa.Column("ocr_started_at", sa.DateTime(timezone=True)))
    op.add_column("doc_versions", sa.Column("ocr_error", sa.Text))
    op.add_column(
        "doc_versions",
        sa.Column("ocr_override", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.create_check_constraint(
        "ck_doc_versions_ocr_status", "doc_versions", f"ocr_status IN {STATES}"
    )
    op.create_index(
        "ix_doc_versions_ocr_queue",
        "doc_versions",
        ["ocr_pages", "ocr_requested_at"],
        postgresql_where=sa.text("ocr_status = 'queued'"),
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; restore a backup to go back.")
