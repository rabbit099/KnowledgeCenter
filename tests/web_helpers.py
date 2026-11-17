"""Helpers for integration tests that log in through the real login form."""

from __future__ import annotations

import re
import uuid

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from baza_wiedzy.core.auth.accounts import ensure_local_admin
from baza_wiedzy.core.db.models import Role, RolePermission, User, UserRole

PASSWORD = "synthetic helper passphrase"
TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def random_ip() -> str:
    octets = uuid.uuid4().bytes[:3]
    return f"10.{octets[0] % 250}.{octets[1] % 250}.{octets[2] % 250 + 1}"


def client(app: FastAPI, ip: str | None = None) -> TestClient:
    c = TestClient(app, base_url="https://testserver", client=(ip or random_ip(), 40000))
    c.__enter__()
    return c


def csrf(c: TestClient, path: str = "/login") -> str:
    match = TOKEN_RE.search(c.get(path).text)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


def make_user(app: FastAPI, *system_roles: str, custom_codes: set[str] | None = None) -> User:
    """Local account with exactly the given system roles (and optionally a custom role)."""
    name = f"u-{uuid.uuid4().hex[:10]}"
    with app.state.session_factory.begin() as db:
        user = ensure_local_admin(db, name, PASSWORD, app.state.authenticator.passwords).user
        db.execute(delete(UserRole).where(UserRole.user_id == user.id))
        for key in system_roles:
            role_id = db.scalar(select(Role.id).where(Role.system_key == key))
            db.add(UserRole(user_id=user.id, role_id=role_id, source="manual"))
        if custom_codes is not None:
            role = Role(name=f"custom-{uuid.uuid4().hex[:8]}")
            db.add(role)
            db.flush()
            for code in custom_codes:
                db.add(RolePermission(role_id=role.id, permission_code=code))
            db.add(UserRole(user_id=user.id, role_id=role.id, source="manual"))
        db.flush()
        db.expunge(user)
    return user


def login(app: FastAPI, user: User) -> TestClient:
    c = client(app)
    response: httpx.Response = c.post(
        "/login",
        data={"username": user.username, "password": PASSWORD, "csrf_token": csrf(c)},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return c
