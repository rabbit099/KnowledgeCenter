"""Audit viewer and CSV export against the real database."""

from __future__ import annotations

import csv
import io
import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from baza_wiedzy.core.audit.service import Actor, record
from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.db.models import AuditEntry
from baza_wiedzy.main import create_app
from web_helpers import csrf, login, make_user

pytestmark = pytest.mark.integration


def _app(settings: Settings, **overrides: object) -> FastAPI:
    return create_app(
        settings.model_copy(update={"db_pool_size": 3, "db_max_overflow": 0, **overrides}),
        setup_logging=False,
    )


def _seed(
    app: FastAPI,
    marker: str,
    count: int = 1,
    *,
    actor: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    with app.state.session_factory.begin() as db:
        for i in range(count):
            record(
                db,
                f"test.{marker}",
                Actor(None, actor if actor and i == 0 else marker),
                entity_type="thing",
                entity_id=f"{marker}-{i}",
                ip="10.1.2.3",
                details=details or {"n": i},
            )


def test_viewer_filters_escapes_and_shows_pairs(settings: Settings) -> None:
    app = _app(settings)
    marker = f"m{uuid.uuid4().hex[:8]}"
    _seed(app, marker, details={"before": {"name": "<b>old</b>"}, "added": ["a.b", "c.d"]})
    c = login(app, make_user(app, "admin"))
    page = c.get(f"/admin/audit?action=test.{marker}")
    assert page.status_code == 200
    assert "&lt;b&gt;old&lt;/b&gt;" in page.text and "<b>old</b>" not in page.text
    assert "before.name" in page.text and "a.b, c.d" in page.text
    fragment = c.get(f"/admin/audit?actor={marker}", headers={"HX-Request": "true"}).text
    assert "<table" in fragment and "<html" not in fragment
    assert "{&#34;" not in fragment and '{"' not in fragment  # pairs, not raw JSON
    empty = c.get(f"/admin/audit?action=test.{marker}&date_to=2000-01-01").text
    assert "Żaden wpis nie pasuje do filtra." in empty


def test_viewer_needs_audit_view(settings: Settings) -> None:
    app = _app(settings)
    c = login(app, make_user(app, "publisher"))
    assert c.get("/admin/audit").status_code == 403
    token = csrf(c, "/")
    assert c.post("/admin/audit/export", data={"csrf_token": token}).status_code == 403
    auditor = login(app, make_user(app, custom_codes={"audit.view"}))
    assert auditor.get("/admin/audit").status_code == 200


def test_export_filters_neutralises_and_records_itself(settings: Settings) -> None:
    app = _app(settings)
    marker = f"m{uuid.uuid4().hex[:8]}"
    _seed(app, marker, actor='=HYPERLINK("http://evil")', details={"-note": "+cmd"})
    admin = make_user(app, "admin")
    c = login(app, admin)
    assert c.post("/admin/audit/export", data={"action": f"test.{marker}"}).status_code == 403
    response = c.post(
        "/admin/audit/export",
        data={"action": f"test.{marker}", "csrf_token": csrf(c, "/admin/audit")},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    text = response.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert rows[0][:6] == ["id", "time_utc", "time_local", "actor", "actor_user_id", "action"]
    assert len(rows) == 2
    assert rows[1][3] == '\'=HYPERLINK("http://evil")'
    assert rows[1][9] == "'-note=+cmd"  # the details cell starts with "-"

    with app.state.session_factory() as db:
        entry = db.scalars(
            select(AuditEntry)
            .where(AuditEntry.action == "audit.export", AuditEntry.actor_user_id == admin.id)
            .order_by(AuditEntry.id.desc())
        ).first()
    assert entry is not None
    assert entry.details["filters"]["action"] == f"test.{marker}"
    assert (entry.details["rows"], entry.details["truncated"]) == (1, False)


def test_export_limit_is_announced(settings: Settings) -> None:
    app = _app(settings, audit_export_max_rows=2)
    marker = f"m{uuid.uuid4().hex[:8]}"
    _seed(app, marker, count=3)
    c = login(app, make_user(app, "admin"))
    page = c.get(f"/admin/audit?action=test.{marker}").text
    assert "Eksport obejmie tylko 2 najnowszych z 3 wpisów" in page
    response = c.post(
        "/admin/audit/export",
        data={"action": f"test.{marker}", "csrf_token": csrf(c, "/admin/audit")},
    )
    assert response.headers["x-export-truncated"] == "true"
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
    assert len(rows) == 4 and rows[-1][0].startswith("TRUNCATED")
    assert [r[7] for r in rows[1:3]] == [f"{marker}-2", f"{marker}-1"]  # newest first
