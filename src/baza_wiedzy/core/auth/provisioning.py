"""Just-in-time AD users and group -> role synchronisation at every AD login (ADR-008).

Identity is the AD objectGUID. An account created in advance by an admin (source ad, no GUID
yet) is linked at the first login by its name (case-insensitive). A local account never becomes
an AD account: a name clash is refused and logged for the administrator.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from baza_wiedzy.core.audit.service import Actor, record
from baza_wiedzy.core.auth.accounts import find_user
from baza_wiedzy.core.auth.ldap import AdUser
from baza_wiedzy.core.db.models import (
    ROLE_SOURCE_AD,
    SOURCE_AD,
    SOURCE_LOCAL,
    AdGroupRoleMap,
    Role,
    User,
    UserRole,
)

log = logging.getLogger(__name__)


class ProvisioningError(Exception):
    """The AD login is valid but cannot be mapped to an application account."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def mapped_role_ids(db: Session, group_dns: tuple[str, ...]) -> set[uuid.UUID]:
    if not group_dns:
        return set()
    lowered = [dn.lower() for dn in group_dns]
    return set(
        db.scalars(
            select(AdGroupRoleMap.role_id).where(
                func.lower(AdGroupRoleMap.ad_group_dn).in_(lowered)
            )
        )
    )


def provision(db: Session, ad: AdUser, *, ip: str | None) -> User:
    now = datetime.now(UTC)
    user = db.scalar(select(User).where(User.ad_object_guid == ad.object_guid))
    by_name = find_user(db, ad.login)
    if user is None:
        if by_name is not None and by_name.source == SOURCE_LOCAL:
            log.error(
                "AD login refused: a local account already uses the name of AD user %s", ad.dn
            )
            raise ProvisioningError("local_name_conflict")
        if by_name is not None and by_name.ad_object_guid not in (None, ad.object_guid):
            log.error("AD login refused: name already linked to another AD object (%s)", ad.dn)
            raise ProvisioningError("guid_mismatch")
        if by_name is not None:  # created in advance by an admin: link it now
            user = by_name
            user.ad_object_guid = ad.object_guid
            db.flush()
            record(
                db,
                "user.ad_link",
                Actor(user.id, user.username),
                entity_type="user",
                entity_id=user.id,
                ip=ip,
                details={"username": user.username},
            )
        else:
            user = User(username=ad.login, source=SOURCE_AD, ad_object_guid=ad.object_guid)
            db.add(user)
            db.flush()
            record(
                db,
                "user.create",
                Actor(user.id, ad.login),
                entity_type="user",
                entity_id=user.id,
                ip=ip,
                details={"username": ad.login, "source": "ad_jit"},
            )
    elif user.username.lower() != ad.login:
        if by_name is not None and by_name.id != user.id:
            log.error("AD login refused: renamed AD user %s clashes with another account", ad.dn)
            raise ProvisioningError("name_taken")
        record(
            db,
            "user.rename",
            Actor(user.id, user.username),
            entity_type="user",
            entity_id=user.id,
            ip=ip,
            details={"old": user.username, "new": ad.login},
        )
        user.username = ad.login
    user.display_name = ad.display_name or ad.login
    user.email = ad.email
    user.ad_dn = ad.dn
    user.updated_at = now
    _sync_roles(db, user, ad, ip)
    return user


def _sync_roles(db: Session, user: User, ad: AdUser, ip: str | None) -> None:
    desired = mapped_role_ids(db, ad.groups)
    current = set(
        db.scalars(
            select(UserRole.role_id).where(
                UserRole.user_id == user.id, UserRole.source == ROLE_SOURCE_AD
            )
        )
    )
    added, removed = desired - current, current - desired
    if not added and not removed:
        return
    if removed:
        db.execute(
            delete(UserRole).where(
                UserRole.user_id == user.id,
                UserRole.source == ROLE_SOURCE_AD,
                UserRole.role_id.in_(removed),
            )
        )
    for role_id in added:
        db.add(UserRole(user_id=user.id, role_id=role_id, source=ROLE_SOURCE_AD))
    rows = db.execute(select(Role.id, Role.name).where(Role.id.in_(added | removed)))
    # Not dict(rows): a Result has keys(), so dict() would treat it as a mapping.
    names = {row_id: name for row_id, name in rows}  # noqa: C416
    record(
        db,
        "user.roles_sync",
        Actor(user.id, user.username),
        entity_type="user",
        entity_id=user.id,
        ip=ip,
        details={
            "added": sorted(names[r] for r in added),
            "removed": sorted(names[r] for r in removed),
            "source": "ad",
        },
    )
