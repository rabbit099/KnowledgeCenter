"""Identity: users, roles, role permissions, user roles, AD group mapping, sessions.

Revision ID: core_0002
Revises: core_0001
Create Date: 2026-11-12

Creates the three system roles (Admin, Publisher, Reader). Admin has no rows in
role_permissions: it always holds the full permission catalog (computed in code). Module
permissions for Publisher and Reader are granted by the modules' own migrations.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "core_0002"
down_revision: str | Sequence[str] | None = "core_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = pg.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def _uuid_pk() -> sa.Column[Any]:
    return sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _now(name: str) -> sa.Column[Any]:
    return sa.Column(name, TS, nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("username", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text),
        sa.Column("email", sa.Text),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("password_hash", sa.Text),
        sa.Column("ad_object_guid", UUID),
        sa.Column("ad_dn", sa.Text),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        _now("created_at"),
        _now("updated_at"),
        sa.Column("last_login_at", TS),
        sa.Column("password_changed_at", TS),
        sa.CheckConstraint("source IN ('local', 'ad')", name="ck_users_source"),
        sa.CheckConstraint(
            "(source = 'local') = (password_hash IS NOT NULL)",
            name="ck_users_password_only_local",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("uq_users_username_lower", "users", [sa.text("lower(username)")], unique=True)
    op.create_index("uq_users_ad_object_guid", "users", ["ad_object_guid"], unique=True)

    op.create_table(
        "roles",
        _uuid_pk(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("system_key", sa.Text),
        _now("created_at"),
        _now("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
    )
    op.create_index("uq_roles_name_lower", "roles", [sa.text("lower(name)")], unique=True)
    op.create_index("uq_roles_system_key", "roles", ["system_key"], unique=True)

    op.create_table(
        "role_permissions",
        sa.Column("role_id", UUID, nullable=False),
        sa.Column("permission_code", sa.Text, nullable=False),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], ondelete="CASCADE", name="fk_role_permissions_role_id_roles"
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_code", name="pk_role_permissions"),
    )

    op.create_table(
        "user_roles",
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("role_id", UUID, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        _now("created_at"),
        sa.CheckConstraint("source IN ('manual', 'ad')", name="ck_user_roles_source"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_user_roles_user_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], ondelete="CASCADE", name="fk_user_roles_role_id_roles"
        ),
        sa.PrimaryKeyConstraint("user_id", "role_id", "source", name="pk_user_roles"),
    )
    op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"])

    op.create_table(
        "ad_group_role_map",
        _uuid_pk(),
        sa.Column("ad_group_dn", sa.Text, nullable=False),
        sa.Column("role_id", UUID, nullable=False),
        _now("created_at"),
        sa.Column("created_by", UUID),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            ondelete="CASCADE",
            name="fk_ad_group_role_map_role_id_roles",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_ad_group_role_map_created_by_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ad_group_role_map"),
    )
    op.create_index(
        "uq_ad_group_role_map_group_role",
        "ad_group_role_map",
        [sa.text("lower(ad_group_dn)"), "role_id"],
        unique=True,
    )

    op.create_table(
        "sessions",
        sa.Column("id_hash", sa.LargeBinary, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        _now("created_at"),
        _now("last_seen_at"),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("ip", pg.INET),
        sa.Column("user_agent", sa.Text),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_sessions_user_id_users"
        ),
        sa.PrimaryKeyConstraint("id_hash", name="pk_sessions"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.execute(
        """
        INSERT INTO roles (name, description, system_key) VALUES
          ('Admin', 'All permissions; cannot be reduced or deleted', 'admin'),
          ('Publisher', 'Uploads and publishes own documents', 'publisher'),
          ('Reader', 'Searches and reads published documents', 'reader')
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only; restore a backup to go back.")
