"""Failed-login limits in Redis: per account, per (account, address) and per address.

Counters use a fixed window; reaching a threshold sets a lock key that expires by itself. User
names are hashed in the keys. Counting works the same for names that do not exist, so a lock
says nothing about whether an account exists. If Redis is unavailable, logins still work (the
limit is skipped) and the error is logged.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import redis

log = logging.getLogger(__name__)
PREFIX = "bw:login:"


@dataclass(frozen=True)
class Limits:
    window_seconds: int
    lockout_seconds: int
    account: int
    account_ip: int
    ip: int


def _name(username: str) -> str:
    return hashlib.sha256(username.strip().lower().encode()).hexdigest()[:32]


class LoginLimiter:
    def __init__(self, client: redis.Redis, limits: Limits) -> None:
        self.client = client
        self.limits = limits

    def _scopes(self, username: str, ip: str | None) -> list[tuple[str, str, int]]:
        name = _name(username)
        scopes = [("account", f"acct:{name}", self.limits.account)]
        if ip:
            scopes.append(("account_ip", f"pair:{name}:{ip}", self.limits.account_ip))
            scopes.append(("ip", f"ip:{ip}", self.limits.ip))
        return scopes

    def blocked(self, username: str, ip: str | None) -> bool:
        keys = [f"{PREFIX}lock:{key}" for _, key, _ in self._scopes(username, ip)]
        try:
            return bool(self.client.exists(*keys))
        except redis.RedisError:
            log.error("login limiter: Redis unavailable, limit not enforced", exc_info=True)
            return False

    def failure(self, username: str, ip: str | None) -> list[str]:
        """Count a failure; return the scopes that became locked now (for the audit log)."""
        locked: list[str] = []
        try:
            for scope, key, threshold in self._scopes(username, ip):
                pipe = self.client.pipeline()
                pipe.incr(f"{PREFIX}fail:{key}")
                pipe.expire(f"{PREFIX}fail:{key}", self.limits.window_seconds, nx=True)
                count = int(pipe.execute()[0])
                if count >= threshold and self.client.set(
                    f"{PREFIX}lock:{key}", "1", ex=self.limits.lockout_seconds, nx=True
                ):
                    locked.append(scope)
        except redis.RedisError:
            log.error("login limiter: Redis unavailable, failure not counted", exc_info=True)
        return locked

    def success(self, username: str, ip: str | None) -> None:
        name = _name(username)
        keys = [f"{PREFIX}fail:acct:{name}"]
        if ip:
            keys.append(f"{PREFIX}fail:pair:{name}:{ip}")
        try:
            self.client.delete(*keys)
        except redis.RedisError:
            log.error("login limiter: Redis unavailable", exc_info=True)

    def unlock(self, *, username: str | None = None, ip: str | None = None) -> int:
        """Remove counters and locks for a user name (all addresses) and/or an address."""
        patterns: list[str] = []
        if username:
            name = _name(username)
            patterns += [f"{PREFIX}*:acct:{name}", f"{PREFIX}*:pair:{name}:*"]
        if ip:
            patterns += [f"{PREFIX}*:ip:{ip}", f"{PREFIX}*:pair:*:{ip}"]
        removed = 0
        for pattern in patterns:
            keys = list(self.client.scan_iter(match=pattern, count=500))
            if keys:
                removed += int(self.client.delete(*keys))
        return removed
