"""Server-side sessions (ADR-007). The cookie holds a random token; the database holds only its
SHA-256. A new session (new token) is created at every login, which defeats session fixation.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from baza_wiedzy.core.db.models import Session as DbSession
from baza_wiedzy.core.db.models import User

TOUCH_INTERVAL = timedelta(minutes=1)  # last_seen_at is written at most once a minute


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@dataclass(frozen=True)
class SessionLimits:
    idle: timedelta
    absolute: timedelta


def create(
    db: Session, user: User, limits: SessionLimits, *, ip: str | None, user_agent: str | None
) -> str:
    """Create a session and return the token for the cookie (never stored)."""
    now = datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    db.add(
        DbSession(
            id_hash=token_hash(token),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + limits.absolute,
            ip=ip,
            user_agent=(user_agent or "")[:512] or None,
        )
    )
    return token


def load(db: Session, token: str, limits: SessionLimits) -> tuple[DbSession, User] | None:
    """Return the valid session and its active user, or None (expired sessions are deleted)."""
    row = db.get(DbSession, token_hash(token))
    if row is None:
        return None
    now = datetime.now(UTC)
    user = db.get(User, row.user_id)
    expired = row.expires_at <= now or row.last_seen_at + limits.idle <= now
    if expired or user is None or not user.is_active:
        db.delete(row)
        return None
    if now - row.last_seen_at >= TOUCH_INTERVAL:
        row.last_seen_at = now
    return row, user


def destroy(db: Session, token: str) -> None:
    db.execute(delete(DbSession).where(DbSession.id_hash == token_hash(token)))


def purge_expired(db: Session, limits: SessionLimits) -> int:
    now = datetime.now(UTC)
    result = db.execute(
        delete(DbSession).where(
            or_(DbSession.expires_at <= now, DbSession.last_seen_at <= now - limits.idle)
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)
