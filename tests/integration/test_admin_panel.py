"""Admin panel end to end (plain form posts with CSRF, as without JavaScript)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ldap3.core.exceptions import LDAPSocketOpenError
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from baza_wiedzy.cli import main
from baza_wiedzy.core.admin.errors import AdminError
from baza_wiedzy.core.admin.users import active_admin_ids, set_active, set_manual_roles
from baza_wiedzy.core.audit.service import CLI_ACTOR, Actor
from baza_wiedzy.core.auth.accounts import ensure_local_admin
from baza_wiedzy.core.auth.ldap import AdDirectory
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.models import (
    AdGroupRoleMap,
    AuditEntry,
    Role,
    RolePermission,
    User,
    UserRole,
)
from baza_wiedzy.main import create_app
from ldap_mock import MockAd

pytestmark = pytest.mark.integration

PASSWORD = "synthetic admin passphrase"
TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(
        settings.model_copy(update={"db_pool_size": 3, "db_max_overflow": 0}),
        setup_logging=False,
    )


@pytest.fixture
def db(app: FastAPI) -> Iterator[Session]:
    with app.state.session_factory() as session:
        yield session


def role_id(db: Session, key: str) -> uuid.UUID:
    found = db.scalar(select(Role.id).where(Role.system_key == key))
    assert found is not None
    return found


@pytest.fixture
def make_user(app: FastAPI, db: Session) -> Callable[..., User]:
    """Local account (created like the break-glass admin), then given exactly `roles`."""

    def make(*roles: str) -> User:
        name = f"u-{uuid.uuid4().hex[:10]}"
        with app.state.session_factory.begin() as tx:
            user = ensure_local_admin(tx, name, PASSWORD, app.state.authenticator.passwords).user
            tx.execute(delete(UserRole).where(UserRole.user_id == user.id))
            for key in roles:
                tx.add(UserRole(user_id=user.id, role_id=role_id(tx, key), source="manual"))
        found = db.get(User, user.id)
        assert found is not None
        return found

    return make


@pytest.fixture
def login_as(app: FastAPI) -> Iterator[Callable[[User], TestClient]]:
    clients: list[TestClient] = []

    def login(user: User) -> TestClient:
        octets = uuid.uuid4().bytes[:3]
        address = f"10.{octets[0] % 250}.{octets[1] % 250}.{octets[2] % 250 + 1}"
        client = TestClient(app, base_url="https://testserver", client=(address, 40000))
        client.__enter__()
        clients.append(client)
        token = csrf(client, "/login")
        response = client.post(
            "/login",
            data={"username": user.username, "password": PASSWORD, "csrf_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 303
        return client

    yield login
    for client in clients:
        client.__exit__(None, None, None)


def csrf(client: TestClient, path: str) -> str:
    match = TOKEN_RE.search(client.get(path).text)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


def post(client: TestClient, path: str, data: dict[str, object], page: str) -> httpx.Response:
    response: httpx.Response = client.post(
        path, data={**data, "csrf_token": csrf(client, page)}, follow_redirects=False
    )
    return response


def last_audit(db: Session, action: str) -> AuditEntry:
    db.expire_all()
    return db.scalars(
        select(AuditEntry)
        .where(AuditEntry.action == action)
        .order_by(AuditEntry.id.desc())
        .limit(1)
    ).one()


def test_admin_navigation_and_pages(
    make_user: Callable[..., User], login_as: Callable[[User], TestClient]
) -> None:
    client = login_as(make_user("admin"))
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/admin/users"
    for path in (
        "/admin/users",
        "/admin/roles",
        "/admin/ad-groups",
        "/admin/system",
        "/admin/users/new",
    ):
        page = client.get(path)
        assert page.status_code == 200, path
        assert "Użytkownicy" in page.text and "Grupy AD" in page.text  # navigation from manifests
    assert "Administracja" in client.get("/").text


def test_reader_gets_403_on_pages_and_htmx_fragments(
    make_user: Callable[..., User], login_as: Callable[[User], TestClient]
) -> None:
    client = login_as(make_user("reader"))
    assert "Administracja" not in client.get("/").text  # the link is hidden...
    for path in ("/admin", "/admin/users", "/admin/roles", "/admin/ad-groups", "/admin/system"):
        assert client.get(path).status_code == 403, path  # ...and the server refuses anyway
        assert client.get(path, headers={"HX-Request": "true"}).status_code == 403, path
    token = csrf(client, "/")
    assert client.post("/admin/roles", data={"name": "x", "csrf_token": token}).status_code == 403


def test_post_without_csrf_token_is_refused(
    make_user: Callable[..., User], login_as: Callable[[User], TestClient]
) -> None:
    client = login_as(make_user("admin"))
    assert client.post("/admin/roles", data={"name": "no-token"}).status_code == 403


def test_role_lifecycle_with_audit_diffs(
    db: Session, make_user: Callable[..., User], login_as: Callable[[User], TestClient]
) -> None:
    client = login_as(make_user("admin"))
    name = f"Editors {uuid.uuid4().hex[:6]}"
    response = post(
        client,
        "/admin/roles",
        {"name": name, "codes": ["documents.read", "audit.view"]},
        "/admin/roles/new",
    )
    assert response.status_code == 303
    role = db.scalar(select(Role).where(Role.name == name))
    assert role is not None
    assert last_audit(db, "role.create").details["permissions"] == ["audit.view", "documents.read"]

    post(
        client,
        f"/admin/roles/{role.id}",
        {"name": name, "codes": ["documents.read", "system.view"]},
        f"/admin/roles/{role.id}",
    )
    change = last_audit(db, "role.permissions_change")
    assert change.details["before"] == ["audit.view", "documents.read"]
    assert change.details["after"] == ["documents.read", "system.view"]
    assert (change.details["added"], change.details["removed"]) == (["system.view"], ["audit.view"])

    holder = make_user()
    db.add(UserRole(user_id=holder.id, role_id=role.id, source="manual"))
    db.commit()
    confirm_page = client.get(f"/admin/roles/{role.id}/delete")
    assert holder.username in confirm_page.text
    refused = post(client, f"/admin/roles/{role.id}/delete", {}, f"/admin/roles/{role.id}/delete")
    assert refused.status_code == 400 and holder.username in refused.text
    deleted = post(
        client,
        f"/admin/roles/{role.id}/delete",
        {"confirm": "yes"},
        f"/admin/roles/{role.id}/delete",
    )
    assert deleted.status_code == 303
    assert last_audit(db, "role.delete").details["affected_users"] == [holder.username]


def test_system_roles_are_protected(
    db: Session, make_user: Callable[..., User], login_as: Callable[[User], TestClient]
) -> None:
    client = login_as(make_user("admin"))
    admin_role = role_id(db, "admin")
    page = client.get(f"/admin/roles/{admin_role}").text
    assert "zawsze ma wszystkie uprawnienia" in page and "disabled" in page
    post(
        client,
        f"/admin/roles/{admin_role}",
        {"name": "Hacked", "codes": ["documents.read"]},
        f"/admin/roles/{admin_role}",
    )
    db.expire_all()
    admin = db.get(Role, admin_role)
    assert admin is not None and admin.name == "Admin"
    assert db.scalars(select(Role).where(Role.id == admin_role)).one().permissions == []
    reader = role_id(db, "reader")
    response = post(
        client, f"/admin/roles/{reader}/delete", {"confirm": "yes"}, f"/admin/roles/{reader}"
    )
    assert response.status_code == 400 and "Ról systemowych nie można usuwać" in response.text


def test_planned_ad_user_names_are_normalized_and_escaped(
    db: Session, make_user: Callable[..., User], login_as: Callable[[User], TestClient]
) -> None:
    client = login_as(make_user("admin"))
    login = f"nowak{uuid.uuid4().hex[:6]}"
    response = post(
        client,
        "/admin/users",
        {
            "username": f"FIRMA\\{login.upper()}",
            "display_name": "<script>alert(1)</script>",
            "role_ids": [str(role_id(db, "reader"))],
        },
        "/admin/users/new",
    )
    assert response.status_code == 303
    created = db.scalar(select(User).where(User.username == login))
    assert created is not None and created.source == "ad" and created.ad_object_guid is None
    listing = client.get(f"/admin/users?q={login}").text
    assert "<script>alert(1)</script>" not in listing
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in listing
    duplicate = post(client, "/admin/users", {"username": login}, "/admin/users/new")
    assert duplicate.status_code == 400 and "już istnieje" in duplicate.text


def test_deactivation_revokes_sessions_and_roles_apply_next_request(
    db: Session, make_user: Callable[..., User], login_as: Callable[[User], TestClient]
) -> None:
    admin_client = login_as(make_user("admin"))
    target = make_user("reader")
    target_client = login_as(target)
    assert target_client.get("/admin/roles").status_code == 403

    custom = Role(name=f"Role managers {uuid.uuid4().hex[:6]}")
    db.add(custom)
    db.flush()
    db.add(RolePermission(role_id=custom.id, permission_code="roles.manage"))
    db.commit()
    page = f"/admin/users/{target.id}"
    post(
        admin_client,
        f"/admin/users/{target.id}/roles",
        {"role_ids": [str(role_id(db, "reader")), str(custom.id)]},
        page,
    )
    assert target_client.get("/admin/roles").status_code == 200  # next request, no new login
    change = last_audit(db, "user.roles_change")
    assert change.details["before"] == ["Reader"] and custom.name in change.details["after"]

    response = post(admin_client, f"/admin/users/{target.id}/active", {"active": "no"}, page)
    assert response.status_code == 303
    assert target_client.get("/", follow_redirects=False).status_code == 303  # session gone
    assert last_audit(db, "user.deactivate").details["revoked_sessions"] == 1


def test_admin_cannot_lock_themselves_out(
    db: Session, make_user: Callable[..., User], login_as: Callable[[User], TestClient]
) -> None:
    me = make_user("admin")
    client = login_as(me)
    page = f"/admin/users/{me.id}"
    response = post(client, f"/admin/users/{me.id}/active", {"active": "no"}, page)
    assert response.status_code == 400 and "własnego konta" in response.text
    response = post(
        client, f"/admin/users/{me.id}/roles", {"role_ids": [str(role_id(db, "reader"))]}, page
    )
    assert response.status_code == 400 and "własnemu kontu" in response.text
    db.expire_all()
    assert me.id in active_admin_ids(db)


def test_last_active_admin_is_protected(app: FastAPI, make_user: Callable[..., User]) -> None:
    last = make_user("admin")
    with app.state.session_factory() as tx:
        # Inside one transaction that is rolled back: every other admin inactive for a moment.
        tx.execute(update(User).where(User.id != last.id).values(is_active=False))
        target = tx.get(User, last.id)
        assert target is not None and active_admin_ids(tx) == {last.id}
        other = Actor(uuid.uuid4(), "another-admin")
        with pytest.raises(AdminError, match="last active administrator"):
            set_active(tx, other, target, False, ip=None)
        with pytest.raises(AdminError, match="last active administrator"):
            set_manual_roles(tx, other, target, set(), ip=None)
        with pytest.raises(AdminError, match="last active administrator"):
            set_active(tx, CLI_ACTOR, target, False, ip=None)  # the CLI uses the same rule
        tx.rollback()


def test_cli_user_active(
    db: Session, make_user: Callable[..., User], settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user("reader")
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    assert main(["user-active", user.username, "off"]) == 0
    db.expire_all()
    refreshed = db.get(User, user.id)
    assert refreshed is not None and not refreshed.is_active
    entry = last_audit(db, "user.deactivate")
    assert entry.actor_label == "cli" and entry.entity_id == str(user.id)
    assert main(["user-active", "no-such-user-x", "on"]) == 1


def test_ad_group_mapping_rules(
    app: FastAPI,
    db: Session,
    make_user: Callable[..., User],
    login_as: Callable[[User], TestClient],
) -> None:
    ad = MockAd()
    existing = ad.add_group(f"BW-{uuid.uuid4().hex[:6]}", [])
    app.state.authenticator.directory = ad.directory()
    client = login_as(make_user("admin"))
    page = "/admin/ad-groups"
    reader, admin = str(role_id(db, "reader")), str(role_id(db, "admin"))

    bad = post(client, page, {"dn": "BazaWiedzy-Readers", "role_id": reader}, page)
    assert bad.status_code == 400 and "To nie jest poprawny DN grupy" in bad.text

    missing = post(
        client,
        page,
        {"dn": "CN=Nope,OU=Groups,DC=firma,DC=local", "role_id": reader, "verify": "yes"},
        page,
    )
    assert missing.status_code == 400 and "Nie znaleziono tej grupy" in missing.text

    found = post(client, page, {"dn": existing, "role_id": reader, "verify": "yes"}, page)
    assert found.headers["location"] == "/admin/ad-groups?done=mapped_found"
    assert last_audit(db, "ad_map.create").details["verification"] == "found"

    unconfirmed = post(client, page, {"dn": existing, "role_id": admin, "verify": "yes"}, page)
    assert unconfirmed.status_code == 400 and "Potwierdzam" in unconfirmed.text
    confirmed = post(
        client,
        page,
        {"dn": existing, "role_id": admin, "verify": "yes", "confirm_admin": "yes"},
        page,
    )
    assert confirmed.status_code == 303

    class Down:
        def bind(self) -> bool:
            raise LDAPSocketOpenError("timed out")

    app.state.authenticator.directory = AdDirectory(
        ad.directory().config, factory=lambda dn, pw: [Down()]
    )
    dn = f"CN=Offline-{uuid.uuid4().hex[:6]},OU=Groups,DC=firma,DC=local"
    offline = post(client, page, {"dn": dn, "role_id": reader, "verify": "yes"}, page)
    assert offline.headers["location"] == "/admin/ad-groups?done=mapped_unavailable"

    mapping = db.scalars(select(AdGroupRoleMap).where(AdGroupRoleMap.ad_group_dn == dn)).one()
    removed = post(client, f"/admin/ad-groups/{mapping.id}/delete", {}, page)
    assert removed.status_code == 303
    assert last_audit(db, "ad_map.delete").details["ad_group_dn"] == dn
    listing = client.get(f"/admin/ad-groups?q={existing.split(',')[0][3:]}").text
    assert "CN=Offline" not in listing and existing.split(",")[0] in listing
