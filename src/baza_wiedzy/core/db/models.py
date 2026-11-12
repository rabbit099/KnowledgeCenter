"""Tables owned by the core: identity, roles, sessions, audit (docs/en/data-model.md).

The migrations in `core/db/migrations/` create them; these models must stay identical (an
integration test compares them with the migrated database).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from baza_wiedzy.core.db.base import Base

SOURCE_LOCAL = "local"
SOURCE_AD = "ad"
ROLE_SOURCE_MANUAL = "manual"
ROLE_SOURCE_AD = "ad"

# Stable keys of the system roles; names may be renamed in the UI, keys never change.
SYSTEM_ROLE_ADMIN = "admin"
SYSTEM_ROLE_PUBLISHER = "publisher"
SYSTEM_ROLE_READER = "reader"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("source IN ('local', 'ad')", name="source"),
        # Local accounts always have a password hash; AD accounts never do.
        CheckConstraint(
            "(source = 'local') = (password_hash IS NOT NULL)", name="password_only_local"
        ),
        Index("uq_users_username_lower", func.lower(text("username")), unique=True),
        Index("uq_users_ad_object_guid", "ad_object_guid", unique=True),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    username: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    ad_object_guid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ad_dn: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list[UserRole]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        Index("uq_roles_name_lower", func.lower(text("name")), unique=True),
        Index("uq_roles_system_key", "system_key", unique=True),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # admin / publisher / reader for the system roles, NULL for roles created by admins.
    system_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    permissions: Mapped[list[RolePermission]] = relationship(
        back_populates="role", cascade="all, delete-orphan", passive_deletes=True
    )


class RolePermission(Base):
    """Permission codes of a role. The Admin role has no rows: it always holds the full catalog."""

    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(Text, primary_key=True)

    role: Mapped[Role] = relationship(back_populates="permissions")


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        CheckConstraint("source IN ('manual', 'ad')", name="source"),
        Index("ix_user_roles_role_id", "role_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    # manual = assigned in the admin UI or CLI; ad = recalculated from AD groups at each login.
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = _now()

    user: Mapped[User] = relationship(back_populates="roles")
    role: Mapped[Role] = relationship()


class AdGroupRoleMap(Base):
    __tablename__ = "ad_group_role_map"
    __table_args__ = (
        Index(
            "uq_ad_group_role_map_group_role",
            func.lower(text("ad_group_dn")),
            "role_id",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    ad_group_dn: Mapped[str] = mapped_column(Text, nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = _now()
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    role: Mapped[Role] = relationship()


class Session(Base):
    """Server-side session. Only the SHA-256 of the cookie token is stored (ADR-007)."""

    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_expires_at", "expires_at"),
    )

    id_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = _now()
    last_seen_at: Mapped[datetime] = _now()
    # Absolute end of the session; the idle limit is checked against last_seen_at.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)


class AuditEntry(Base):
    """Append-only (a trigger rejects UPDATE, DELETE and TRUNCATE). No foreign keys on purpose."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_at", "at"),
        Index("ix_audit_log_actor_user_id", "actor_user_id"),
        Index("ix_audit_log_action", "action"),
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = _now()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # User name at the time of the event, or "cli" / "system".
    actor_label: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
