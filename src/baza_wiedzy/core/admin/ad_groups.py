"""Mapping of AD groups (by DN) to roles. Mapping a group to Admin needs an explicit
confirmation. If AD is configured, the group can be looked up first; if AD cannot be asked, the
mapping is saved without the check (the admin is told so)."""

from __future__ import annotations

import uuid
from typing import Any

from ldap3.core.exceptions import LDAPInvalidDnError
from ldap3.utils.dn import parse_dn
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from baza_wiedzy.core.admin.errors import AdminError
from baza_wiedzy.core.audit.service import Actor, record
from baza_wiedzy.core.auth.ldap import AdDirectory, AdUnavailable
from baza_wiedzy.core.db.models import SYSTEM_ROLE_ADMIN, AdGroupRoleMap, Role
from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.core.web.pagination import Page, paginate

MAX_DN = 1024


class NeedsAdminConfirmation(AdminError):
    def __init__(self) -> None:
        super().__init__(
            N_(
                "Members of this group will get the Admin role with all permissions. "
                "Tick the confirmation to save."
            )
        )


def validate_dn(raw: str) -> str:
    """A syntactically valid DN with a CN or OU and DC components, e.g.
    CN=BazaWiedzy-Readers,OU=Groups,DC=firma,DC=local."""
    dn = raw.strip()
    try:
        parts = parse_dn(dn) if 0 < len(dn) <= MAX_DN else []
    except (LDAPInvalidDnError, ValueError, IndexError):
        parts = []
    attributes = [attr.upper() for attr, _, _ in parts]
    if not parts or attributes[0] not in {"CN", "OU"} or "DC" not in attributes:
        raise AdminError(
            N_("Not a valid group DN. Example: CN=BazaWiedzy-Readers,OU=Groups,DC=firma,DC=local")
        )
    return dn


def mappings_query(q: str = "") -> Select[Any]:
    query = (
        select(AdGroupRoleMap)
        .join(Role, Role.id == AdGroupRoleMap.role_id)
        .order_by(func.lower(AdGroupRoleMap.ad_group_dn), func.lower(Role.name))
    )
    if q:
        like = f"%{q.lower()}%"
        query = query.where(
            or_(func.lower(AdGroupRoleMap.ad_group_dn).like(like), func.lower(Role.name).like(like))
        )
    return query


def list_mappings(db: Session, *, q: str, page: int) -> Page[AdGroupRoleMap]:
    return paginate(db, mappings_query(q), page)


def create_mapping(
    db: Session,
    actor: Actor,
    *,
    dn: str,
    role_id: uuid.UUID,
    confirm_admin: bool,
    verify: bool,
    directory: AdDirectory | None,
    ip: str | None,
) -> tuple[AdGroupRoleMap, str]:
    """Returns the mapping and the verification result: found, not_checked or unavailable."""
    dn = validate_dn(dn)
    role = db.get(Role, role_id)
    if role is None:
        raise AdminError(N_("One of the selected roles does not exist any more."))
    duplicate = db.scalar(
        select(AdGroupRoleMap).where(
            func.lower(AdGroupRoleMap.ad_group_dn) == dn.lower(),
            AdGroupRoleMap.role_id == role.id,
        )
    )
    if duplicate is not None:
        raise AdminError(N_("This group is already mapped to this role."))
    if role.system_key == SYSTEM_ROLE_ADMIN and not confirm_admin:
        raise NeedsAdminConfirmation()
    verification = "not_checked"
    if verify and directory is not None:
        try:
            if not directory.group_exists(dn):
                raise AdminError(N_("The group was not found in Active Directory."))
            verification = "found"
        except AdUnavailable:
            verification = "unavailable"
    mapping = AdGroupRoleMap(ad_group_dn=dn, role_id=role.id, created_by=actor.user_id)
    db.add(mapping)
    db.flush()
    record(
        db,
        "ad_map.create",
        actor,
        entity_type="ad_group_role_map",
        entity_id=mapping.id,
        ip=ip,
        details={"ad_group_dn": dn, "role": role.name, "verification": verification},
    )
    return mapping, verification


def delete_mapping(db: Session, actor: Actor, mapping: AdGroupRoleMap, *, ip: str | None) -> None:
    role = db.get(Role, mapping.role_id)
    record(
        db,
        "ad_map.delete",
        actor,
        entity_type="ad_group_role_map",
        entity_id=mapping.id,
        ip=ip,
        details={"ad_group_dn": mapping.ad_group_dn, "role": role.name if role else None},
    )
    db.delete(mapping)
