"""`baza-wiedzy create-admin`: create, reset, refuse AD names and weak passwords."""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from baza_wiedzy.cli import main
from baza_wiedzy.core.auth.passwords import Passwords
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.models import AuditEntry, Role, User, UserRole
from baza_wiedzy.core.db.models import Session as DbSession

pytestmark = pytest.mark.integration

PASSWORD = "synthetic test passphrase 1"


@pytest.fixture
def db(settings: Settings) -> Iterator[Session]:
    engine = create_engine(settings.database_url)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


@pytest.fixture
def cli_env(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", settings.database_url)


def run(monkeypatch: pytest.MonkeyPatch, *args: str, stdin: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return main(["create-admin", *args, "--password-stdin"])


def audit_actions(db: Session, user_id: uuid.UUID) -> list[str]:
    rows = db.scalars(
        select(AuditEntry.action)
        .where(AuditEntry.entity_id == str(user_id))
        .order_by(AuditEntry.id)
    )
    return list(rows)


@pytest.mark.usefixtures("cli_env")
def test_create_then_reset(
    db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    name = f"bw-admin-{uuid.uuid4().hex[:8]}"
    assert run(monkeypatch, name, stdin=PASSWORD + "\n") == 0
    user = db.scalar(select(User).where(User.username == name))
    assert user is not None and user.source == "local" and user.is_active
    assert user.password_hash is not None and user.password_hash.startswith("$argon2id$")
    assert Passwords(max_concurrent=1, max_length=256).verify(user.password_hash, PASSWORD)
    roles = db.scalars(
        select(Role.system_key)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id)
    ).all()
    assert roles == ["admin"]

    # Reset: deactivated account with an open session -> active again, session revoked.
    user.is_active = False
    db.add(
        DbSession(
            id_hash=uuid.uuid4().bytes * 2,
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db.commit()
    new_password = "another synthetic passphrase"
    assert run(monkeypatch, name.upper(), stdin=new_password) == 0
    db.expire_all()
    user = db.get(User, user.id)
    assert user is not None and user.is_active
    assert Passwords(max_concurrent=1, max_length=256).verify(user.password_hash, new_password)
    assert db.scalars(select(DbSession).where(DbSession.user_id == user.id)).all() == []
    assert audit_actions(db, user.id) == ["cli.create_admin", "cli.reset_password"]

    output = capsys.readouterr()
    assert "1 session(s) revoked" in output.out
    for secret in (PASSWORD, new_password):
        assert secret not in output.out + output.err


@pytest.mark.usefixtures("cli_env")
def test_ad_account_name_is_refused(
    db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    name = f"jan.kowalski-{uuid.uuid4().hex[:8]}"
    db.add(User(username=name, source="ad"))
    db.commit()
    assert run(monkeypatch, name, stdin=PASSWORD) == 1
    assert "Active Directory account" in capsys.readouterr().err


@pytest.mark.usefixtures("cli_env")
def test_weak_password_changes_nothing(
    db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    name = f"bw-admin-{uuid.uuid4().hex[:8]}"
    assert run(monkeypatch, name, stdin="too short") == 1
    assert "at least 14 characters" in capsys.readouterr().err
    assert db.scalar(select(User).where(User.username == name)) is None
