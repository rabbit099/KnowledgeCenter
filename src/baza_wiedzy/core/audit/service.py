"""Writing audit entries. Entries are added in the caller's transaction, so they are committed
together with the change they describe (or not at all).

Never put passwords, tokens or document content into `details`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from baza_wiedzy.core.db.models import AuditEntry

# Keys that must never appear in details (defence against accidental logging of secrets).
FORBIDDEN_DETAIL_KEYS = frozenset({"password", "password_hash", "token", "secret", "csrf"})


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID | None
    label: str


CLI_ACTOR = Actor(None, "cli")
ANONYMOUS = Actor(None, "anonymous")


def _check_details(details: Mapping[str, Any]) -> None:
    for key, value in details.items():
        if any(word in key.lower() for word in FORBIDDEN_DETAIL_KEYS):
            raise ValueError(f"audit details must not contain {key!r}")
        if isinstance(value, Mapping):
            _check_details(value)


def record(
    db: Session,
    action: str,
    actor: Actor,
    *,
    entity_type: str | None = None,
    entity_id: str | uuid.UUID | None = None,
    ip: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> None:
    payload = dict(details or {})
    _check_details(payload)
    db.add(
        AuditEntry(
            actor_user_id=actor.user_id,
            actor_label=actor.label,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            ip=ip,
            details=payload,
        )
    )
