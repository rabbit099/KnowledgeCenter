"""Login limiter behaviour without Redis (failure modes); thresholds are tested with Redis."""

from __future__ import annotations

from typing import Any

import pytest
import redis

from baza_wiedzy.core.auth.ratelimit import Limits, LoginLimiter

LIMITS = Limits(window_seconds=900, lockout_seconds=900, account=30, account_ip=5, ip=20)


class BrokenRedis:
    def __getattr__(self, name: str) -> Any:
        def fail(*_: Any, **__: Any) -> Any:
            raise redis.ConnectionError("down")

        return fail


def test_without_redis_logins_are_not_blocked(caplog: pytest.LogCaptureFixture) -> None:
    limiter = LoginLimiter(BrokenRedis(), LIMITS)  # type: ignore[arg-type]
    assert limiter.blocked("jan", "10.0.0.1") is False
    assert limiter.failure("jan", "10.0.0.1") == []
    limiter.success("jan", "10.0.0.1")
    assert "Redis unavailable" in caplog.text


def test_keys_do_not_contain_the_user_name() -> None:
    limiter = LoginLimiter(BrokenRedis(), LIMITS)  # type: ignore[arg-type]
    keys = [key for _, key, _ in limiter._scopes("Jan.Kowalski", "10.0.0.1")]
    assert all("kowalski" not in key.lower() for key in keys)
    assert [scope for scope, _, _ in limiter._scopes("Jan.Kowalski", "10.0.0.1")] == [
        "account",
        "account_ip",
        "ip",
    ]
