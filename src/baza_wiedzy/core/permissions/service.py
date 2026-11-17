"""Effective permissions of a user (union of roles; Admin = the full catalog)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from baza_wiedzy.core.db.models import SYSTEM_ROLE_ADMIN, Role, RolePermission, User, UserRole


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    username: str
    display_name: str
    source: str
    is_admin: bool
    permissions: frozenset[str] = field(default_factory=frozenset)

    def can(self, code: str) -> bool:
        return code in self.permissions


def effective_permissions(
    db: Session, user_id: uuid.UUID, catalog: frozenset[str]
) -> tuple[bool, frozenset[str]]:
    """(is_admin, codes). Codes of disabled modules are ignored (not in the catalog)."""
    keys = set(
        db.scalars(
            select(Role.system_key)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
    )
    if SYSTEM_ROLE_ADMIN in keys:
        return True, catalog
    codes = db.scalars(
        select(RolePermission.permission_code)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
    )
    return False, frozenset(codes) & catalog


def current_user(db: Session, user: User, catalog: frozenset[str]) -> CurrentUser:
    is_admin, codes = effective_permissions(db, user.id, catalog)
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        source=user.source,
        is_admin=is_admin,
        permissions=codes,
    )
