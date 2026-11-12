"""Login and logout. One generic failure message, whatever the reason (CLAUDE.md section 8)."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from baza_wiedzy.core.audit.service import ANONYMOUS, Actor, record
from baza_wiedzy.core.auth import sessions
from baza_wiedzy.core.auth.accounts import find_user
from baza_wiedzy.core.auth.passwords import Passwords
from baza_wiedzy.core.auth.ratelimit import LoginLimiter
from baza_wiedzy.core.db.models import SOURCE_LOCAL, User


class Outcome(enum.Enum):
    OK = "ok"
    FAILED = "failed"  # wrong password, unknown or inactive account, AD error: same answer
    LOCKED = "locked"  # too many failures for this name and/or address


@dataclass(frozen=True)
class LoginResult:
    outcome: Outcome
    token: str | None = None
    user: User | None = None


@dataclass
class Authenticator:
    passwords: Passwords
    limiter: LoginLimiter
    limits: sessions.SessionLimits

    def login(
        self,
        db: Session,
        username: str,
        password: str,
        *,
        ip: str | None,
        user_agent: str | None,
        previous_token: str | None,
    ) -> LoginResult:
        username = username.strip()
        user = find_user(db, username) if username else None
        actor = Actor(user.id, user.username) if user is not None else ANONYMOUS
        if self.limiter.blocked(username, ip):
            self._failed(db, actor, user, ip, reason="locked")
            return LoginResult(Outcome.LOCKED)

        verified = False
        if user is not None and user.source == SOURCE_LOCAL:
            verified = self.passwords.verify(user.password_hash, password)
        else:
            # AD accounts are verified by the AD provider (stage B4). Until then, and for unknown
            # names, spend the same Argon2 time as for a local account.
            self.passwords.verify(None, password)

        if not verified or user is None or not user.is_active:
            reason = "inactive" if verified and user is not None else "bad_credentials"
            self._failed(db, actor, user, ip, reason=reason)
            for scope in self.limiter.failure(username, ip):
                record(
                    db,
                    "auth.lockout",
                    actor,
                    entity_type="user" if user else None,
                    entity_id=user.id if user else None,
                    ip=ip,
                    details={"scope": scope, "known_user": user is not None},
                )
            db.commit()
            return LoginResult(Outcome.FAILED)

        self.limiter.success(username, ip)
        if previous_token:
            sessions.destroy(db, previous_token)  # never reuse a session id across a login
        sessions.purge_expired(db, self.limits)
        if user.password_hash and self.passwords.needs_rehash(user.password_hash):
            user.password_hash = self.passwords.hash(password)
        user.last_login_at = datetime.now(UTC)
        token = sessions.create(db, user, self.limits, ip=ip, user_agent=user_agent)
        record(
            db,
            "auth.login",
            actor,
            entity_type="user",
            entity_id=user.id,
            ip=ip,
            details={"source": user.source},
        )
        db.commit()
        return LoginResult(Outcome.OK, token=token, user=user)

    @staticmethod
    def _failed(db: Session, actor: Actor, user: User | None, ip: str | None, reason: str) -> None:
        # The typed name is stored only for existing accounts (people sometimes type a password
        # into the name field).
        record(
            db,
            "auth.login_failed",
            actor,
            entity_type="user" if user else None,
            entity_id=user.id if user else None,
            ip=ip,
            details={"reason": reason, "known_user": user is not None},
        )
        db.commit()

    def logout(self, db: Session, token: str, actor: Actor, ip: str | None) -> None:
        sessions.destroy(db, token)
        record(db, "auth.logout", actor, entity_type="user", entity_id=actor.user_id, ip=ip)
        db.commit()
