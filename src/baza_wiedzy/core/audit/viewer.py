"""Reading the audit log: filters, pages (newest first), readable details, CSV export.

CSV cells that start with = + - @ (or a tab / carriage return) are prefixed with an apostrophe,
so spreadsheet programs do not run them as formulas (CSV injection).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from baza_wiedzy.core.db.models import AuditEntry
from baza_wiedzy.core.web.pagination import Page, paginate

LOCAL_TZ = ZoneInfo("Europe/Warsaw")
EMPTY = "\u2013"  # en dash, shown for empty values
DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
CSV_COLUMNS = (
    "id",
    "time_utc",
    "time_local",
    "actor",
    "actor_user_id",
    "action",
    "entity_type",
    "entity_id",
    "ip",
    "details",
)


@dataclass(frozen=True)
class AuditFilters:
    date_from: date | None = None  # inclusive, local (Europe/Warsaw) day
    date_to: date | None = None  # inclusive, local day
    actor: str = ""
    action: str = ""
    entity: str = ""  # entity type or id (contains)

    @classmethod
    def parse(cls, raw: dict[str, str]) -> AuditFilters:
        def day(value: str) -> date | None:
            try:
                return date.fromisoformat(value) if value else None
            except ValueError:
                return None

        return cls(
            date_from=day(raw.get("date_from", "")),
            date_to=day(raw.get("date_to", "")),
            actor=raw.get("actor", "").strip()[:200],
            action=raw.get("action", "").strip()[:100],
            entity=raw.get("entity", "").strip()[:200],
        )

    def as_query(self) -> dict[str, str]:
        return {
            "date_from": self.date_from.isoformat() if self.date_from else "",
            "date_to": self.date_to.isoformat() if self.date_to else "",
            "actor": self.actor,
            "action": self.action,
            "entity": self.entity,
        }


def _local_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=LOCAL_TZ)


def query(filters: AuditFilters) -> Select[Any]:
    stmt = select(AuditEntry).order_by(AuditEntry.at.desc(), AuditEntry.id.desc())
    if filters.date_from:
        stmt = stmt.where(AuditEntry.at >= _local_midnight(filters.date_from))
    if filters.date_to:
        stmt = stmt.where(AuditEntry.at < _local_midnight(filters.date_to + timedelta(days=1)))
    if filters.actor:
        actor = filters.actor.lower()
        stmt = stmt.where(func.lower(AuditEntry.actor_label).contains(actor, autoescape=True))
    if filters.action:
        # "role." finds every role action; a full name finds exactly that action.
        if filters.action.endswith("."):
            stmt = stmt.where(AuditEntry.action.startswith(filters.action, autoescape=True))
        else:
            stmt = stmt.where(AuditEntry.action == filters.action)
    if filters.entity:
        needle = filters.entity.lower()
        stmt = stmt.where(
            func.lower(func.coalesce(AuditEntry.entity_type, "")).contains(needle, autoescape=True)
            | func.lower(func.coalesce(AuditEntry.entity_id, "")).contains(needle, autoescape=True)
        )
    return stmt


def list_entries(db: Session, filters: AuditFilters, page: int) -> Page[AuditEntry]:
    return paginate(db, query(filters), page, size=50)


def known_actions(db: Session) -> list[str]:
    return list(db.scalars(select(AuditEntry.action).distinct().order_by(AuditEntry.action)))


def detail_pairs(details: dict[str, Any] | None, prefix: str = "") -> list[tuple[str, str]]:
    """Flatten details into readable (key, value) pairs: nested keys joined with '.', lists
    joined with ', ', empty values shown as an en dash."""
    pairs: list[tuple[str, str]] = []
    for key, value in (details or {}).items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            pairs.extend(detail_pairs(value, f"{name}."))
        elif isinstance(value, list | tuple):
            pairs.append((name, ", ".join(str(v) for v in value) or EMPTY))
        elif value is None or value == "":
            pairs.append((name, EMPTY))
        elif isinstance(value, bool):
            pairs.append((name, "true" if value else "false"))
        else:
            pairs.append((name, str(value)))
    return pairs


def neutralize(value: str) -> str:
    return "'" + value if value.startswith(DANGEROUS_PREFIXES) else value


def export_csv(db: Session, filters: AuditFilters, max_rows: int) -> tuple[str, int, bool]:
    """CSV text (UTF-8 with BOM, ';' separator for spreadsheets set to Polish), row count, cut?"""
    rows = list(db.scalars(query(filters).limit(max_rows + 1)))
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for entry in rows:
        details = "; ".join(f"{k}={v}" for k, v in detail_pairs(entry.details))
        writer.writerow(
            [
                neutralize(str(value))
                for value in (
                    entry.id,
                    entry.at.isoformat(),
                    entry.at.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                    entry.actor_label,
                    entry.actor_user_id or "",
                    entry.action,
                    entry.entity_type or "",
                    entry.entity_id or "",
                    entry.ip or "",
                    details,
                )
            ]
        )
    if truncated:
        message = f"TRUNCATED: only the newest {max_rows} entries; narrow the filters for the rest"
        writer.writerow([message])
    return "﻿" + buffer.getvalue(), len(rows), truncated
