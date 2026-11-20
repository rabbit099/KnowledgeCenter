"""User accounts: lookup and the local break-glass admin (CLI `baza-wiedzy create-admin`)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from baza_wiedzy.core.audit.service import CLI_ACTOR, record
from baza_wiedzy.core.auth.passwords import Passwords
from baza_wiedzy.core.db.models import (
    ROLE_SOURCE_MANUAL,
    SOURCE_AD,
    SOURCE_LOCAL,
    SYSTEM_ROLE_ADMIN,
    Role,
    User,
    UserRole,
)
from baza_wiedzy.core.db.models import Session as DbSession

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")


class AccountError(Exception):
    """A readable error for operators (CLI) and admins (UI)."""


def valid_username(username: str) -> bool:
    return bool(USERNAME_RE.match(username))


def find_user(db: Session, username: str) -> User | None:
    """Case-insensitive lookup across local and AD accounts."""
    return db.scalar(select(User).where(func.lower(User.username) == username.lower()))


def system_role(db: Session, key: str) -> Role:
    role = db.scalar(select(Role).where(Role.system_key == key))
    if role is None:
        raise AccountError(f"System role {key!r} is missing; run `baza-wiedzy migrate`.")
    return role


def revoke_sessions(db: Session, user: User) -> int:
    result = db.execute(delete(DbSession).where(DbSession.user_id == user.id))
    return int(getattr(result, "rowcount", 0) or 0)


@dataclass(frozen=True)
class BreakGlassResult:
    user: User
    created: bool
    revoked_sessions: int


def ensure_local_admin(
    db: Session, username: str, normalized_password: str, passwords: Passwords
) -> BreakGlassResult:
    """Create the local admin, or reset its password, reactivate it and restore the Admin role.

    The caller has already checked the password policy. Resetting revokes all sessions of the
    account. Everything happens in the caller's transaction, together with the audit entry.
    """
    if not valid_username(username):
        raise AccountError(
            "Invalid user name: use 1-128 characters: letters, digits, '.', '_', '@', '-'."
        )
    admin = system_role(db, SYSTEM_ROLE_ADMIN)
    now = datetime.now(UTC)
    user = find_user(db, username)
    if user is not None and user.source == SOURCE_AD:
        raise AccountError(
            f"{user.username!r} is an Active Directory account; choose another name for the "
            "local break-glass admin (for example bw-admin)."
        )
    password_hash = passwords.hash(normalized_password)
    created = user is None
    revoked = 0
    if user is None:
        user = User(
            username=username,
            source=SOURCE_LOCAL,
            password_hash=password_hash,
            display_name=username,
            password_changed_at=now,
        )
        db.add(user)
        db.flush()
    else:
        user.password_hash = password_hash
        user.password_changed_at = now
        user.is_active = True
        user.updated_at = now
        revoked = revoke_sessions(db, user)
    has_admin = db.scalar(
        select(UserRole).where(
            UserRole.user_id == user.id,
            UserRole.role_id == admin.id,
            UserRole.source == ROLE_SOURCE_MANUAL,
        )
    )
    if has_admin is None:
        db.add(UserRole(user_id=user.id, role_id=admin.id, source=ROLE_SOURCE_MANUAL))
    record(
        db,
        "cli.create_admin" if created else "cli.reset_password",
        CLI_ACTOR,
        entity_type="user",
        entity_id=user.id,
        details={"username": user.username, "revoked_sessions": revoked},
    )
    return BreakGlassResult(user=user, created=created, revoked_sessions=revoked)


def reset_local_password(
    db: Session, username: str, normalized_password: str, passwords: Passwords
) -> tuple[User, int]:
    """Set a new password for a LOCAL account (operator, CLI); returns (user, revoked sessions).

    The caller has checked the password policy. Active Directory accounts are refused: their
    password lives in AD. The account's state and roles are not changed (unlike the break-glass
    `create-admin`). All sessions of the account are revoked; the audit entry
    `user.password_reset_cli` never contains the password or its hash. There is deliberately no
    reset by e-mail: company accounts are in AD, local accounts are few (ADR-029).
    """
    user = find_user(db, username)
    if user is None:
        raise AccountError(f"No user {username!r}.")
    if user.source != SOURCE_LOCAL:
        raise AccountError(
            f"{user.username!r} is an Active Directory account: its password is managed in "
            "Active Directory, not here. Nothing was changed."
        )
    now = datetime.now(UTC)
    user.password_hash = passwords.hash(normalized_password)
    user.password_changed_at = now
    user.updated_at = now
    revoked = revoke_sessions(db, user)
    record(
        db,
        "user.password_reset_cli",
        CLI_ACTOR,
        entity_type="user",
        entity_id=user.id,
        details={"username": user.username, "revoked_sessions": revoked},
    )
    return user, revoked
