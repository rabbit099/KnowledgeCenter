"""Login, logout, sessions, limits and CSRF against the real database and Redis."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from baza_wiedzy.cli import main
from baza_wiedzy.core.auth.accounts import ensure_local_admin
from baza_wiedzy.core.auth.sessions import token_hash
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.models import AuditEntry, User
from baza_wiedzy.core.db.models import Session as DbSession
from baza_wiedzy.main import create_app

pytestmark = pytest.mark.integration

PASSWORD = "synthetic login passphrase"
SESSION = "__Host-bw_session"
TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    # The test itself holds a connection (fixture `db`), so the app needs more than one.
    return create_app(
        settings.model_copy(update={"db_pool_size": 3, "db_max_overflow": 0}),
        setup_logging=False,
    )


@pytest.fixture
def db(app: FastAPI) -> Iterator[Session]:
    with app.state.session_factory() as session:
        yield session


@pytest.fixture
def new_client(app: FastAPI) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def make(ip: str | None = None) -> TestClient:
        # A distinct client address per test keeps the per-address limits independent.
        octets = uuid.uuid4().bytes[:3]
        address = ip or f"10.{octets[0] % 250}.{octets[1] % 250}.{octets[2] % 250 + 1}"
        client = TestClient(app, base_url="https://testserver", client=(address, 40000))
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def admin(app: FastAPI, db: Session) -> User:
    name = f"bw-admin-{uuid.uuid4().hex[:8]}"
    passwords = app.state.authenticator.passwords
    with app.state.session_factory.begin() as tx:
        result = ensure_local_admin(tx, name, PASSWORD, passwords)
    user = db.get(User, result.user.id)
    assert user is not None
    return user


def csrf(client: TestClient, path: str = "/login") -> str:
    match = TOKEN_RE.search(client.get(path).text)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


def login(client: TestClient, username: str, password: str, next_url: str = "/") -> httpx.Response:
    token = csrf(client)
    response: httpx.Response = client.post(
        "/login",
        data={"username": username, "password": password, "next": next_url, "csrf_token": token},
        follow_redirects=False,
    )
    return response


def actions_for(db: Session, user_id: uuid.UUID) -> list[str]:
    db.expire_all()
    return list(
        db.scalars(
            select(AuditEntry.action)
            .where(AuditEntry.actor_user_id == user_id)
            .order_by(AuditEntry.id)
        )
    )


def test_login_page_flow_and_logout(
    new_client: Callable[..., TestClient], admin: User, db: Session
) -> None:
    client = new_client()
    response = login(client, admin.username.upper(), PASSWORD)  # names ignore case
    assert response.status_code == 303 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    for flag in (f"{SESSION}=", "HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert flag.lower() in cookie.lower()
    home = client.get("/")
    assert home.status_code == 200 and admin.username in home.text

    token = client.cookies.get(SESSION)
    assert token is not None
    assert client.post("/logout", follow_redirects=False).status_code == 403  # no CSRF token
    response = client.post(
        "/logout", data={"csrf_token": csrf(client, "/")}, follow_redirects=False
    )
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert db.get(DbSession, token_hash(token)) is None

    replay = new_client()
    replay.cookies.set(SESSION, token, domain="testserver.local")  # worthless after logout
    assert replay.get("/", follow_redirects=False).status_code == 303
    assert actions_for(db, admin.id)[-2:] == ["auth.login", "auth.logout"]


def test_get_logout_redirects_to_the_login_page_and_logs_nobody_out(
    new_client: Callable[..., TestClient], admin: User, db: Session
) -> None:
    anonymous = new_client().get("/logout", follow_redirects=False)
    assert anonymous.status_code == 303 and anonymous.headers["location"] == "/login"
    client = new_client()
    login(client, admin.username, PASSWORD)
    token = client.cookies.get(SESSION)
    assert token is not None
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert "set-cookie" not in response.headers  # the session cookie is not touched
    assert db.get(DbSession, token_hash(token)) is not None  # still logged in: GET changes nothing
    assert client.get("/", follow_redirects=False).status_code == 200
    assert actions_for(db, admin.id)[-1] == "auth.login"


def test_failure_does_not_reveal_whether_the_account_exists(
    new_client: Callable[..., TestClient], admin: User, db: Session
) -> None:
    client = new_client()
    wrong_password = login(client, admin.username, "wrong password value")
    unknown_user = login(client, f"nobody-{uuid.uuid4().hex[:8]}", "wrong password value")
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.text == unknown_user.text
    assert "Nieprawidłowa nazwa użytkownika lub hasło." in unknown_user.text

    db.expire_all()
    anonymous = db.scalars(
        select(AuditEntry)
        .where(AuditEntry.action == "auth.login_failed", AuditEntry.actor_user_id.is_(None))
        .order_by(AuditEntry.id.desc())
        .limit(1)
    ).one()
    assert anonymous.actor_label == "anonymous"
    assert anonymous.details == {"reason": "bad_credentials", "known_user": False}
    assert actions_for(db, admin.id)[-1] == "auth.login_failed"


def test_login_rotates_the_session_id(
    new_client: Callable[..., TestClient], admin: User, db: Session
) -> None:
    client = new_client()
    # Session fixation attempt: a cookie planted for the site before the victim logs in.
    client.cookies.set(SESSION, "attacker-chosen-token", domain="testserver.local")
    login(client, admin.username, PASSWORD)
    first = client.cookies.get(SESSION)
    assert first and first != "attacker-chosen-token"

    login(client, admin.username, PASSWORD)  # logging in again replaces the session
    second = client.cookies.get(SESSION)
    assert second and second != first
    assert db.get(DbSession, token_hash(first)) is None
    assert db.get(DbSession, token_hash(second)) is not None


@pytest.mark.parametrize("column", ["last_seen_at", "expires_at"])
def test_idle_and_absolute_expiry(
    new_client: Callable[..., TestClient], admin: User, db: Session, column: str
) -> None:
    client = new_client()
    login(client, admin.username, PASSWORD)
    token = client.cookies.get(SESSION)
    assert token is not None
    past = datetime.now(UTC) - timedelta(hours=13)
    db.execute(
        update(DbSession).where(DbSession.id_hash == token_hash(token)).values({column: past})
    )
    db.commit()
    assert client.get("/", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(DbSession, token_hash(token)) is None


def test_deactivated_user_loses_session_and_cannot_log_in(
    new_client: Callable[..., TestClient], admin: User, db: Session
) -> None:
    client = new_client()
    login(client, admin.username, PASSWORD)
    db.execute(update(User).where(User.id == admin.id).values(is_active=False))
    db.commit()
    assert client.get("/", follow_redirects=False).status_code == 303
    assert login(client, admin.username, PASSWORD).status_code == 401


def test_lock_per_name_and_address_then_cli_unlock(
    new_client: Callable[..., TestClient],
    admin: User,
    db: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ip = "10.250.1.7"
    client = new_client(ip)
    for _ in range(settings.login_max_failures_account_ip):
        assert login(client, admin.username, "wrong").status_code == 401
    locked = login(client, admin.username, PASSWORD)
    assert locked.status_code == 429
    assert "Zbyt wiele nieudanych prób" in locked.text

    # Another address is not affected (the account threshold is much higher).
    other = new_client("10.250.1.8")
    assert login(other, admin.username, PASSWORD).status_code == 303

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("REDIS_URL", settings.redis_url)
    assert main(["unlock-login", "--user", admin.username, "--ip", ip]) == 0
    assert login(client, admin.username, PASSWORD).status_code == 303
    actions = actions_for(db, admin.id)
    assert "auth.lockout" in actions
    unlock = db.scalars(
        select(AuditEntry).where(
            AuditEntry.action == "cli.unlock_login", AuditEntry.entity_id == str(admin.id)
        )
    ).one()
    assert unlock.actor_label == "cli" and unlock.details["ip"] == ip


def test_login_without_csrf_token_is_refused(
    new_client: Callable[..., TestClient], admin: User
) -> None:
    client = new_client()
    client.get("/login")
    response = client.post("/login", data={"username": admin.username, "password": PASSWORD})
    assert response.status_code == 403
    assert client.cookies.get(SESSION) is None


def test_next_parameter_cannot_leave_the_site(
    new_client: Callable[..., TestClient], admin: User
) -> None:
    client = new_client()
    response = login(client, admin.username, PASSWORD, next_url="//evil.example/x")
    assert response.headers["location"] == "/"
    response = login(client, admin.username, PASSWORD, next_url="/status")
    assert response.headers["location"] == "/status"
