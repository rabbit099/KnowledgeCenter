"""Guards of the audit writer (no database needed for the guard itself)."""

from __future__ import annotations

from typing import Any

import pytest

from baza_wiedzy.core.audit.service import CLI_ACTOR, record


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)


@pytest.mark.parametrize(
    "details",
    [{"password": "x"}, {"new_password_hash": "x"}, {"nested": {"Token": "x"}}, {"csrf": "x"}],
)
def test_secret_keys_are_refused(details: dict[str, Any]) -> None:
    db: Any = FakeSession()
    with pytest.raises(ValueError, match="must not contain"):
        record(db, "test.action", CLI_ACTOR, details=details)
    assert db.added == []


def test_entry_fields() -> None:
    db: Any = FakeSession()
    record(db, "test.action", CLI_ACTOR, entity_type="user", entity_id="42", details={"a": 1})
    [entry] = db.added
    assert (entry.action, entry.actor_label, entry.entity_id) == ("test.action", "cli", "42")
    assert entry.details == {"a": 1}
