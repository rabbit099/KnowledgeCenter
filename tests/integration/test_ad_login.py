"""Active Directory login end to end: mock directory (ldap3 MOCK_SYNC), real DB and Redis."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ldap3.core.exceptions import LDAPSocketOpenError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from baza_wiedzy.core.auth.accounts import ensure_local_admin
from baza_wiedzy.core.auth.ldap import AdDirectory
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.models import AdGroupRoleMap, AuditEntry, Role, User, UserRole
from baza_wiedzy.main import create_app
from ldap_mock import MockAd

pytestmark = pytest.mark.integration

AD_PASSWORD = "synthetic AD user password"
LOCAL_PASSWORD = "synthetic local passphrase"
TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def ad() -> MockAd:
    return MockAd()


@pytest.fixture
def app(settings: Settings, ad: MockAd) -> FastAPI:
    app = create_app(
        settings.model_copy(update={"db_pool_size": 3, "db_max_overflow": 0}),
        setup_logging=False,
    )
    app.state.authenticator.directory = ad.directory()
    return app


@pytest.fixture
def db(app: FastAPI) -> Iterator[Session]:
    with app.state.session_factory() as session:
        yield session


@pytest.fixture
def client(app: FastAPI) -> Iterator[Callable[[], TestClient]]:
    made: list[TestClient] = []

    def make() -> TestClient:
        octets = uuid.uuid4().bytes[:3]
        address = f"10.{octets[0] % 250}.{octets[1] % 250}.{octets[2] % 250 + 1}"
        c = TestClient(app, base_url="https://testserver", client=(address, 40000))
        c.__enter__()
        made.append(c)
        return c

    yield make
    for c in made:
        c.__exit__(None, None, None)


def login(c: TestClient, username: str, password: str) -> httpx.Response:
    match = TOKEN_RE.search(c.get("/login").text)
    assert match
    response: httpx.Response = c.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": match.group(1)},
        follow_redirects=False,
    )
    return response


def role_id(db: Session, key: str) -> uuid.UUID:
    found = db.scalar(select(Role.id).where(Role.system_key == key))
    assert found is not None
    return found


def ad_roles(db: Session, user_id: uuid.UUID) -> set[str]:
    db.expire_all()
    return set(
        db.scalars(
            select(Role.system_key)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id, UserRole.source == "ad")
        )
    )


def unique_login() -> str:
    return f"jan{uuid.uuid4().hex[:8]}"


def test_first_login_creates_user_with_roles_from_groups(
    ad: MockAd, db: Session, client: Callable[[], TestClient]
) -> None:
    login_name = unique_login()
    dn, guid = ad.add_user(login_name, AD_PASSWORD, display_name="Jan Kowalski")
    group = ad.add_group(f"BW-Readers-{login_name}", [dn])
    db.add(AdGroupRoleMap(ad_group_dn=group.upper(), role_id=role_id(db, "reader")))  # any case
    db.commit()

    c = client()
    assert login(c, login_name, AD_PASSWORD).status_code == 303
    user = db.scalar(select(User).where(User.ad_object_guid == guid))
    assert user is not None and user.source == "ad" and user.username == login_name
    assert user.display_name == "Jan Kowalski" and user.password_hash is None
    assert ad_roles(db, user.id) == {"reader"}
    assert "Jan Kowalski" in c.get("/").text
    actions = list(
        db.scalars(select(AuditEntry.action).where(AuditEntry.entity_id == str(user.id)))
    )
    assert {"user.create", "user.roles_sync", "auth.login"} <= set(actions)


def test_domain_and_upn_spellings_are_one_account(
    ad: MockAd, db: Session, client: Callable[[], TestClient]
) -> None:
    login_name = unique_login()
    _, guid = ad.add_user(login_name, AD_PASSWORD)
    for typed in (f"FIRMA\\{login_name}", f"{login_name.upper()}@firma.local", login_name):
        assert login(client(), typed, AD_PASSWORD).status_code == 303, typed
    count = db.scalar(select(func.count()).select_from(User).where(User.ad_object_guid == guid))
    assert count == 1


def test_group_changes_are_synced_and_manual_roles_kept(
    ad: MockAd, db: Session, client: Callable[[], TestClient]
) -> None:
    login_name = unique_login()
    dn, guid = ad.add_user(login_name, AD_PASSWORD)
    readers = ad.add_group(f"R-{login_name}", [dn])
    publishers = ad.add_group(f"P-{login_name}", [])
    db.add_all(
        [
            AdGroupRoleMap(ad_group_dn=readers, role_id=role_id(db, "reader")),
            AdGroupRoleMap(ad_group_dn=publishers, role_id=role_id(db, "publisher")),
        ]
    )
    db.commit()
    login(client(), login_name, AD_PASSWORD)
    user = db.scalar(select(User).where(User.ad_object_guid == guid))
    assert user is not None and ad_roles(db, user.id) == {"reader"}
    db.add(UserRole(user_id=user.id, role_id=role_id(db, "reader"), source="manual"))
    db.commit()

    ad.set_members(readers, [])
    ad.set_members(publishers, [dn])
    login(client(), login_name, AD_PASSWORD)
    assert ad_roles(db, user.id) == {"publisher"}
    manual = db.scalars(
        select(UserRole).where(UserRole.user_id == user.id, UserRole.source == "manual")
    ).all()
    assert len(manual) == 1  # roles given in the application survive the AD sync


def test_account_created_in_advance_is_linked(
    ad: MockAd, db: Session, client: Callable[[], TestClient]
) -> None:
    login_name = unique_login()
    planned = User(username=login_name.upper(), source="ad")
    db.add(planned)
    db.commit()
    _, guid = ad.add_user(login_name, AD_PASSWORD)
    assert login(client(), login_name, AD_PASSWORD).status_code == 303
    db.expire_all()
    linked = db.get(User, planned.id)
    assert linked is not None and linked.ad_object_guid == guid


@pytest.mark.parametrize("case", ["wrong", "disabled", "unknown", "blank"])
def test_ad_failures_look_like_local_failures(
    ad: MockAd, client: Callable[[], TestClient], case: str
) -> None:
    login_name = unique_login()
    ad.add_user(login_name, AD_PASSWORD, disabled=case == "disabled")
    password = {"wrong": "not the password", "blank": "   "}.get(case, AD_PASSWORD)
    name = f"nobody{login_name}" if case == "unknown" else login_name
    c = client()
    response = login(c, name, password)
    reference = login(c, f"nobody-{uuid.uuid4().hex[:6]}", "x")
    assert response.status_code == 401
    assert response.text == reference.text  # same page, same message


def test_local_name_is_never_taken_over_by_ad(
    app: FastAPI, ad: MockAd, db: Session, client: Callable[[], TestClient]
) -> None:
    name = f"bw-admin-{uuid.uuid4().hex[:6]}"
    with app.state.session_factory.begin() as tx:
        ensure_local_admin(tx, name, LOCAL_PASSWORD, app.state.authenticator.passwords)
    ad.add_user(name, AD_PASSWORD)  # an AD account with the same name
    assert login(client(), name, AD_PASSWORD).status_code == 401  # AD password: refused
    assert login(client(), name, LOCAL_PASSWORD).status_code == 303  # local one works
    count = db.scalar(
        select(func.count()).select_from(User).where(func.lower(User.username) == name)
    )
    assert count == 1


def test_ad_unavailable_keeps_break_glass_working(
    app: FastAPI, db: Session, client: Callable[[], TestClient]
) -> None:
    class Down:
        def bind(self) -> bool:
            raise LDAPSocketOpenError("timed out")

    app.state.authenticator.directory = AdDirectory(
        app.state.authenticator.directory.config, factory=lambda dn, pw: [Down()]
    )
    login_name = unique_login()
    response = login(client(), login_name, AD_PASSWORD)
    assert response.status_code == 503
    assert "chwilowo niedostępne" in response.text
    entry = db.scalars(
        select(AuditEntry)
        .where(AuditEntry.action == "auth.ad_unavailable")
        .order_by(AuditEntry.id.desc())
        .limit(1)
    ).one()
    assert entry.details == {"reason": "unreachable", "known_user": False}

    name = f"bw-admin-{uuid.uuid4().hex[:6]}"
    with app.state.session_factory.begin() as tx:
        ensure_local_admin(tx, name, LOCAL_PASSWORD, app.state.authenticator.passwords)
    assert login(client(), name, LOCAL_PASSWORD).status_code == 303


def test_limits_count_all_spellings_together(
    ad: MockAd, settings: Settings, client: Callable[[], TestClient]
) -> None:
    login_name = unique_login()
    ad.add_user(login_name, AD_PASSWORD)
    c = client()
    spellings = [login_name, f"FIRMA\\{login_name}", f"{login_name}@firma.local"]
    for i in range(settings.login_max_failures_account_ip):
        assert login(c, spellings[i % 3], "wrong").status_code == 401
    assert login(c, login_name.upper(), AD_PASSWORD).status_code == 429


def test_user_without_mapped_groups_logs_in_without_permissions(
    ad: MockAd, client: Callable[[], TestClient]
) -> None:
    login_name = unique_login()
    ad.add_user(login_name, AD_PASSWORD)
    c = client()
    assert login(c, login_name, AD_PASSWORD).status_code == 303
    assert "nie ma jeszcze żadnych uprawnień" in c.get("/").text
