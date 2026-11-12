"""Core baseline: database extensions.

Revision ID: core_0001
Revises:
Create Date: 2026-11-12

`pg_trgm` and `unaccent` are trusted extensions (PostgreSQL 13+): the database owner may
create them without superuser rights (docs/en/deployment-operations.md, Configuration).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "core_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("core",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public")


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; restore a backup to go back.")
