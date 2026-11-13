"""Login and logout. One generic failure message, whatever the reason (CLAUDE.md section 8)."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from baza_wiedzy.core.audit.service import ANONYMOUS, Actor, record
from baza_wiedzy.core.auth import sessions
from baza_wiedzy.core.auth.accounts import find_user
from baza_wiedzy.core.auth.ldap import AdDirectory, AdUnavailable
from baza_wiedzy.core.auth.passwords import Passwords
from baza_wiedzy.core.auth.provisioning import ProvisioningError, provision
from baza_wiedzy.core.auth.ratelimit import LoginLimiter
from baza_wiedzy.core.db.models import SOURCE_LOCAL, User


class Outcome(enum.Enum):
    OK = "ok"
    FAILED = "failed"  # wrong password, unknown or inactive account, AD error: same answer
    LOCKED = "locked"  # too many failures for this name and/or address
    AD_UNAVAILABLE = "ad_unavailable"  # the directory could not be asked; local login works


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
    directory: AdDirectory | None = None

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
        raw = username.strip()
        found = find_user(db, raw) if raw else None
        local = found if found is not None and found.source == SOURCE_LOCAL else None
        # AD names are normalized ("FIRMA\\jan", "jan@firma.local" -> one person), also for the
        # limits, so that switching the spelling does not reset the counters.
        use_ad = local is None and self.directory is not None
        username = self.directory.normalize(raw) if use_ad and self.directory else raw
        user = local if local is not None else (find_user(db, username) if username else None)
        actor = Actor(user.id, user.username) if user is not None else ANONYMOUS
        if self.limiter.blocked(username, ip):
            self._failed(db, actor, user, ip, reason="locked")
            return LoginResult(Outcome.LOCKED)

        verified = False
        reason = "bad_credentials"
        if local is not None:
            verified = self.passwords.verify(local.password_hash, password)
        elif use_ad and self.directory is not None and username and password.strip():
            try:
                ad_user = self.directory.authenticate(username, password)
            except AdUnavailable as exc:
                record(
                    db,
                    "auth.ad_unavailable",
                    actor,
                    ip=ip,
                    details={"reason": exc.reason, "known_user": user is not None},
                )
                db.commit()
                return LoginResult(Outcome.AD_UNAVAILABLE)
            if ad_user is not None:
                try:
                    user = provision(db, ad_user, ip=ip)
                    verified = True
                    actor = Actor(user.id, user.username)
                except ProvisioningError as exc:
                    db.rollback()
                    reason = exc.reason
        else:
            # Unknown name without AD, or empty password: spend the same Argon2 time anyway.
            self.passwords.verify(None, password)

        if not verified or user is None or not user.is_active:
            if verified and user is not None:
                reason = "inactive"
                db.rollback()  # do not keep AD profile/role changes of an inactive account
                user = find_user(db, username)
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
