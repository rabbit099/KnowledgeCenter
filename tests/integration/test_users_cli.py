"""`baza-wiedzy users list` and `baza-wiedzy users set-password` (ADR-029)."""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator

import pytest
import redis
from fastapi import FastAPI
from sqlalchemy import select

from baza_wiedzy.cli import main
from baza_wiedzy.core.auth.ratelimit import Limits, LoginLimiter
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.models import AuditEntry, User
from baza_wiedzy.main import create_app
from web_helpers import PASSWORD, client, csrf, login, make_user

pytestmark = pytest.mark.integration

NEW_PASSWORD = "synthetic new passphrase 42"


@pytest.fixture
def app(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("REDIS_URL", settings.redis_url)
    monkeypatch.setenv("SECRET_KEY", settings.secret_key)
    return create_app(settings.model_copy(update={"db_pool_size": 2}), setup_logging=False)


@pytest.fixture
def limiter(settings: Settings) -> Iterator[LoginLimiter]:
    client_ = redis.Redis.from_url(settings.redis_url)
    yield LoginLimiter(
        client_, Limits(window_seconds=60, lockout_seconds=60, account=1, account_ip=1, ip=100)
    )
    client_.close()


def set_password(monkeypatch: pytest.MonkeyPatch, username: str, stdin: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return main(["users", "set-password", username, "--password-stdin"])


def can_log_in(app: FastAPI, username: str, password: str) -> bool:
    c = client(app)
    response = c.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": csrf(c)},
        follow_redirects=False,
    )
    return bool(response.status_code == 303)


def test_users_list_shows_login_source_state_roles_and_last_login(
    app: FastAPI, capsys: pytest.CaptureFixture[str]
) -> None:
    user = make_user(app, "publisher")
    login(app, user)  # sets last_login_at
    with app.state.session_factory.begin() as db:
        ad = User(
            username=f"ad-{uuid.uuid4().hex[:8]}",
            source="ad",
            email="x@example.com",
            ad_dn="CN=Someone,DC=example,DC=com",
        )
        db.add(ad)
        db.flush()
        ad_name = ad.username
    assert main(["users", "list"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].split()[:4] == ["LOGIN", "SOURCE", "ACTIVE", "ROLES"]
    line = next(row for row in out.splitlines() if row.startswith(user.username + " "))
    assert " local " in line and " yes " in line and "Publisher" in line and "never" not in line
    ad_line = next(row for row in out.splitlines() if row.startswith(ad_name + " "))
    assert " ad " in ad_line and ad_line.rstrip().endswith("never")
    assert "$argon2" not in out and "x@example.com" not in out and "CN=Someone" not in out


def test_set_password_for_a_local_account(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    limiter: LoginLimiter,
) -> None:
    user = make_user(app, "publisher")
    session = login(app, user)
    assert session.get("/documents").status_code == 200
    limiter.failure(user.username, "10.0.0.9")
    assert limiter.blocked(user.username, None)
    assert set_password(monkeypatch, user.username, NEW_PASSWORD + "\n") == 0
    out = capsys.readouterr().out
    assert f"Password changed for {user.username!r}; 1 session(s) revoked" in out
    assert "login locks cleared" in out and NEW_PASSWORD not in out
    assert not limiter.blocked(user.username, None)
    assert session.get("/documents", follow_redirects=False).status_code == 303  # logged out
    assert can_log_in(app, user.username, NEW_PASSWORD)
    assert not can_log_in(app, user.username, PASSWORD)
    with app.state.session_factory() as db:
        stored = db.get(User, user.id)
        entry = db.scalars(
            select(AuditEntry).where(
                AuditEntry.action == "user.password_reset_cli", AuditEntry.entity_id == str(user.id)
            )
        ).one()
    assert stored is not None and stored.password_changed_at is not None
    assert entry.actor_label == "cli"
    assert entry.details == {"username": user.username, "revoked_sessions": 1}
    assert NEW_PASSWORD not in str(entry.details) and "argon2" not in str(entry.details)


def test_set_password_refuses_ad_accounts_unknown_users_and_weak_passwords(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with app.state.session_factory.begin() as db:
        ad = User(username=f"ad-{uuid.uuid4().hex[:8]}", source="ad")
        db.add(ad)
        db.flush()
        ad_name = ad.username
    assert set_password(monkeypatch, ad_name, NEW_PASSWORD) == 1
    assert "Active Directory account" in capsys.readouterr().err
    assert set_password(monkeypatch, "nobody-" + uuid.uuid4().hex[:6], NEW_PASSWORD) == 1
    assert "No user" in capsys.readouterr().err
    user = make_user(app, "reader")
    assert set_password(monkeypatch, user.username, "short") == 1
    assert "Nothing was changed" in capsys.readouterr().err
    assert can_log_in(app, user.username, PASSWORD)
    with app.state.session_factory() as db:
        assert (
            db.scalars(
                select(AuditEntry).where(
                    AuditEntry.action == "user.password_reset_cli",
                    AuditEntry.entity_id.in_([str(user.id)]),
                )
            ).first()
            is None
        )
    with pytest.raises(SystemExit) as refused:  # the password is never an argument
        main(["users", "set-password", user.username, NEW_PASSWORD])
    assert refused.value.code == 2
