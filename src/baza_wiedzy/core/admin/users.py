"""User management: list, accounts created in advance for AD, activation, manual roles,
session revocation. Every change is audited in the same transaction.

Self-protection: an admin cannot deactivate themselves or remove their own Admin role, and the
last active admin can never be deactivated or lose the Admin role (panel and CLI).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.orm import Session

from baza_wiedzy.core.admin.errors import AdminError
from baza_wiedzy.core.audit.service import Actor, record
from baza_wiedzy.core.auth.accounts import find_user, revoke_sessions, valid_username
from baza_wiedzy.core.db.models import (
    ROLE_SOURCE_MANUAL,
    SOURCE_AD,
    SYSTEM_ROLE_ADMIN,
    Role,
    User,
    UserRole,
)
from baza_wiedzy.core.db.models import Session as DbSession
from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.core.web.pagination import Page, paginate


def users_query(q: str = "", source: str = "", active: str = "") -> Select[Any]:
    query = select(User).order_by(func.lower(User.username))
    if q:
        like = f"%{q.lower()}%"
        query = query.where(
            or_(
                func.lower(User.username).like(like),
                func.lower(func.coalesce(User.display_name, "")).like(like),
                func.lower(func.coalesce(User.email, "")).like(like),
            )
        )
    if source in {"local", "ad"}:
        query = query.where(User.source == source)
    if active in {"yes", "no"}:
        query = query.where(User.is_active.is_(active == "yes"))
    return query


def list_users(db: Session, *, q: str, source: str, active: str, page: int) -> Page[User]:
    return paginate(db, users_query(q, source, active), page)


def roles_of(db: Session, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[tuple[Role, str]]]:
    result: dict[uuid.UUID, list[tuple[Role, str]]] = defaultdict(list)
    if not user_ids:
        return result
    rows = db.execute(
        select(UserRole.user_id, Role, UserRole.source)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id.in_(user_ids))
        .order_by(func.lower(Role.name))
    )
    for user_id, role, source in rows:
        result[user_id].append((role, source))
    return result


def session_count(db: Session, user_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).where(DbSession.user_id == user_id)) or 0


def _admin_role_id(db: Session) -> uuid.UUID:
    role_id = db.scalar(select(Role.id).where(Role.system_key == SYSTEM_ROLE_ADMIN))
    if role_id is None:
        raise AdminError(N_("The Admin role is missing; run the migrations."))
    return role_id


def active_admin_ids(db: Session) -> set[uuid.UUID]:
    return set(
        db.scalars(
            select(User.id)
            .join(UserRole, UserRole.user_id == User.id)
            .where(UserRole.role_id == _admin_role_id(db), User.is_active.is_(True))
        )
    )


def _role_names(db: Session, role_ids: set[uuid.UUID]) -> list[str]:
    if not role_ids:
        return []
    return sorted(db.scalars(select(Role.name).where(Role.id.in_(role_ids))))


def _manual_role_ids(db: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        db.scalars(
            select(UserRole.role_id).where(
                UserRole.user_id == user_id, UserRole.source == ROLE_SOURCE_MANUAL
            )
        )
    )


def _all_role_ids(db: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    return set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == user_id)))


def _check_roles_exist(db: Session, role_ids: set[uuid.UUID]) -> None:
    found = set(db.scalars(select(Role.id).where(Role.id.in_(role_ids)))) if role_ids else set()
    if found != role_ids:
        raise AdminError(N_("One of the selected roles does not exist any more."))


def create_planned_ad_user(
    db: Session,
    actor: Actor,
    *,
    username: str,
    display_name: str,
    role_ids: set[uuid.UUID],
    ip: str | None,
) -> User:
    """Account for an AD user who has not logged in yet; linked by name at the first login."""
    if not username or not valid_username(username):
        raise AdminError(
            N_("Invalid user name: use letters, digits and '.', '_', '@', '-' (up to 128).")
        )
    if find_user(db, username) is not None:
        raise AdminError(N_("A user with this name already exists."))
    _check_roles_exist(db, role_ids)
    user = User(username=username, source=SOURCE_AD, display_name=display_name or None)
    db.add(user)
    db.flush()
    for role_id in role_ids:
        db.add(UserRole(user_id=user.id, role_id=role_id, source=ROLE_SOURCE_MANUAL))
    record(
        db,
        "user.create",
        actor,
        entity_type="user",
        entity_id=user.id,
        ip=ip,
        details={"username": username, "source": "ad_planned", "roles": _role_names(db, role_ids)},
    )
    return user


def set_active(db: Session, actor: Actor, target: User, active: bool, *, ip: str | None) -> int:
    """Activate or deactivate. Deactivation revokes all sessions at once. Returns revoked count."""
    if target.is_active == active:
        return 0
    revoked = 0
    if not active:
        if actor.user_id == target.id:
            raise AdminError(N_("You cannot deactivate your own account."))
        if target.id in active_admin_ids(db) and len(active_admin_ids(db) - {target.id}) == 0:
            raise AdminError(
                N_("This is the last active administrator; activate or create another first.")
            )
        revoked = revoke_sessions(db, target)
    target.is_active = active
    target.updated_at = datetime.now(UTC)
    record(
        db,
        "user.activate" if active else "user.deactivate",
        actor,
        entity_type="user",
        entity_id=target.id,
        ip=ip,
        details={"username": target.username, "revoked_sessions": revoked},
    )
    return revoked


def set_manual_roles(
    db: Session, actor: Actor, target: User, role_ids: set[uuid.UUID], *, ip: str | None
) -> None:
    """Replace the roles given in the application (roles from AD groups are not touched)."""
    _check_roles_exist(db, role_ids)
    admin_id = _admin_role_id(db)
    before = _manual_role_ids(db, target.id)
    if before == role_ids:
        return
    others = _all_role_ids(db, target.id) - before  # roles from AD groups
    still_admin = admin_id in (others | role_ids)
    if admin_id in before and admin_id not in role_ids and actor.user_id == target.id:
        raise AdminError(N_("You cannot remove the Admin role from your own account."))
    if (
        not still_admin
        and target.id in active_admin_ids(db)
        and len(active_admin_ids(db) - {target.id}) == 0
    ):
        raise AdminError(N_("This is the last active administrator; the Admin role must stay."))
    db.execute(
        delete(UserRole).where(UserRole.user_id == target.id, UserRole.source == ROLE_SOURCE_MANUAL)
    )
    for role_id in role_ids:
        db.add(UserRole(user_id=target.id, role_id=role_id, source=ROLE_SOURCE_MANUAL))
    record(
        db,
        "user.roles_change",
        actor,
        entity_type="user",
        entity_id=target.id,
        ip=ip,
        details={
            "username": target.username,
            "before": _role_names(db, before),
            "after": _role_names(db, role_ids),
            "source": "manual",
        },
    )


def revoke_user_sessions(db: Session, actor: Actor, target: User, *, ip: str | None) -> int:
    revoked = revoke_sessions(db, target)
    record(
        db,
        "session.revoke",
        actor,
        entity_type="user",
        entity_id=target.id,
        ip=ip,
        details={"username": target.username, "revoked_sessions": revoked},
    )
    return revoked
