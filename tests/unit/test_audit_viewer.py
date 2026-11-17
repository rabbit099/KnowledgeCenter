"""Audit viewer helpers: readable details, filter parsing, CSV injection neutralisation."""

from __future__ import annotations

from datetime import date

import pytest

from baza_wiedzy.core.audit.viewer import EMPTY, AuditFilters, detail_pairs, neutralize


def test_details_become_readable_pairs() -> None:
    pairs = detail_pairs(
        {
            "role": "Editors",
            "added": ["audit.view", "documents.read"],
            "removed": [],
            "before": {"name": "A", "description": None},
            "known_user": False,
            "rows": 3,
        }
    )
    assert pairs == [
        ("role", "Editors"),
        ("added", "audit.view, documents.read"),
        ("removed", EMPTY),
        ("before.name", "A"),
        ("before.description", EMPTY),
        ("known_user", "false"),
        ("rows", "3"),
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('=HYPERLINK("http://x")', '\'=HYPERLINK("http://x")'),
        ("+48 123", "'+48 123"),
        ("-1+1", "'-1+1"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tcmd", "'\tcmd"),
        ("auth.login", "auth.login"),
        ("10.0.0.1", "10.0.0.1"),
    ],
)
def test_csv_cells_are_neutralised(value: str, expected: str) -> None:
    assert neutralize(value) == expected


def test_filters_parse_and_ignore_garbage() -> None:
    filters = AuditFilters.parse(
        {"date_from": "2026-11-01", "date_to": "not a date", "actor": " jan ", "action": "role."}
    )
    assert filters.date_from == date(2026, 11, 1) and filters.date_to is None
    assert (filters.actor, filters.action, filters.entity) == ("jan", "role.", "")
    assert filters.as_query()["date_to"] == ""
