"""documents: grant the module's permissions to the default roles Publisher and Reader.

Revision ID: documents_0001
Revises:
Create Date: 2026-11-12

The core creates the system roles (core_0002) but does not know module permission codes; each
module grants its own. Admin needs no grants (it always holds the full catalog). Admins can
change these permissions later in the UI.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "documents_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("documents",)
depends_on: str | Sequence[str] | None = ("core_0002",)

GRANTS = {
    "reader": ("documents.read",),
    "publisher": ("documents.read", "documents.upload", "documents.publish"),
}


def upgrade() -> None:
    for system_key, codes in GRANTS.items():
        for code in codes:
            op.execute(
                f"""
                INSERT INTO role_permissions (role_id, permission_code)
                SELECT id, '{code}' FROM roles WHERE system_key = '{system_key}'
                ON CONFLICT DO NOTHING
                """
            )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; restore a backup to go back.")
