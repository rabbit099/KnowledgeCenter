"""Roles and their permissions. The catalog of codes comes from the module manifests.

System roles (Admin, Publisher, Reader) cannot be deleted or renamed. Admin always holds the full
catalog and is not editable in the matrix. Codes stored for a disabled module are kept when a role
is saved (they are not in the catalog, so they are not shown). Every change is audited with the
codes before and after.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.orm import Session

from baza_wiedzy.core.admin.errors import AdminError
from baza_wiedzy.core.audit.service import Actor, record
from baza_wiedzy.core.db.models import SYSTEM_ROLE_ADMIN, Role, RolePermission, User, UserRole
from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.core.web.pagination import Page, paginate

MAX_NAME = 64
# System role names are data ("Admin", ...) but shown translated; marked here for extraction.
SYSTEM_ROLE_LABELS = (N_("Admin"), N_("Publisher"), N_("Reader"))


class RoleInUse(AdminError):
    """Deleting a role that users hold needs an explicit confirmation."""

    def __init__(self, users: list[User]) -> None:
        super().__init__(N_("This role is assigned to users; confirm to delete it."))
        self.users = users


def roles_query(q: str = "") -> Select[Any]:
    query = select(Role).order_by(Role.system_key.is_(None), func.lower(Role.name))
    if q:
        like = f"%{q.lower()}%"
        query = query.where(
            or_(
                func.lower(Role.name).like(like),
                func.lower(func.coalesce(Role.description, "")).like(like),
            )
        )
    return query


def list_roles(db: Session, *, q: str, page: int) -> Page[Role]:
    return paginate(db, roles_query(q), page)


def all_roles(db: Session) -> list[Role]:
    return list(db.scalars(roles_query()))


def user_counts(db: Session, role_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not role_ids:
        return {}
    rows = db.execute(
        select(UserRole.role_id, func.count(func.distinct(UserRole.user_id)))
        .where(UserRole.role_id.in_(role_ids))
        .group_by(UserRole.role_id)
    )
    return {role_id: int(count) for role_id, count in rows}


def stored_codes(db: Session, role: Role) -> set[str]:
    return set(
        db.scalars(select(RolePermission.permission_code).where(RolePermission.role_id == role.id))
    )


def users_with_role(db: Session, role: Role) -> list[User]:
    return list(
        db.scalars(
            select(User)
            .join(UserRole, UserRole.user_id == User.id)
            .where(UserRole.role_id == role.id)
            .distinct()
            .order_by(User.username)
        )
    )


def _clean_name(db: Session, name: str, role: Role | None) -> str:
    name = " ".join(name.split())
    if not name or len(name) > MAX_NAME:
        raise AdminError(N_("The role name must have 1 to 64 characters."))
    clash = db.scalar(select(Role).where(func.lower(Role.name) == name.lower()))
    if clash is not None and (role is None or clash.id != role.id):
        raise AdminError(N_("A role with this name already exists."))
    return name


def _check_codes(codes: set[str], catalog: frozenset[str]) -> None:
    if not codes <= catalog:
        raise AdminError(N_("Unknown permission code."))


def create_role(
    db: Session,
    actor: Actor,
    *,
    name: str,
    description: str,
    codes: set[str],
    catalog: frozenset[str],
    ip: str | None,
) -> Role:
    _check_codes(codes, catalog)
    role = Role(name=_clean_name(db, name, None), description=description.strip() or None)
    db.add(role)
    db.flush()
    for code in sorted(codes):
        db.add(RolePermission(role_id=role.id, permission_code=code))
    record(
        db,
        "role.create",
        actor,
        entity_type="role",
        entity_id=role.id,
        ip=ip,
        details={"name": role.name, "permissions": sorted(codes)},
    )
    return role


def update_role(
    db: Session,
    actor: Actor,
    role: Role,
    *,
    name: str,
    description: str,
    codes: set[str],
    catalog: frozenset[str],
    ip: str | None,
) -> None:
    before_name, before_description = role.name, role.description
    new_name = role.name if role.system_key else _clean_name(db, name, role)
    new_description = description.strip() or None
    if (new_name, new_description) != (before_name, before_description):
        role.name, role.description = new_name, new_description
        role.updated_at = datetime.now(UTC)
        record(
            db,
            "role.update",
            actor,
            entity_type="role",
            entity_id=role.id,
            ip=ip,
            details={
                "before": {"name": before_name, "description": before_description},
                "after": {"name": new_name, "description": new_description},
            },
        )
    if role.system_key == SYSTEM_ROLE_ADMIN:
        return  # always the full catalog; the matrix is read-only
    _check_codes(codes, catalog)
    before = stored_codes(db, role)
    # Codes of disabled modules are not in the catalog and therefore not in the form: keep them.
    after = codes | (before - catalog)
    if after == before:
        return
    db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    for code in sorted(after):
        db.add(RolePermission(role_id=role.id, permission_code=code))
    role.updated_at = datetime.now(UTC)
    record(
        db,
        "role.permissions_change",
        actor,
        entity_type="role",
        entity_id=role.id,
        ip=ip,
        details={
            "role": role.name,
            "before": sorted(before),
            "after": sorted(after),
            "added": sorted(after - before),
            "removed": sorted(before - after),
        },
    )


def delete_role(db: Session, actor: Actor, role: Role, *, confirmed: bool, ip: str | None) -> None:
    if role.system_key:
        raise AdminError(N_("System roles cannot be deleted."))
    users = users_with_role(db, role)
    if users and not confirmed:
        raise RoleInUse(users)
    record(
        db,
        "role.delete",
        actor,
        entity_type="role",
        entity_id=role.id,
        ip=ip,
        details={
            "name": role.name,
            "permissions": sorted(stored_codes(db, role)),
            "affected_users": [u.username for u in users],
        },
    )
    db.delete(role)  # role_permissions, user_roles and AD mappings cascade
