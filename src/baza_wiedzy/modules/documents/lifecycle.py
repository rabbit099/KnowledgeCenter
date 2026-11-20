"""Document lifecycle: publish, unpublish, archive, restore (Phase 3, ADR-025).

    action      from                 to          own document          other people's document
    publish     draft                published   documents.publish     documents.publish_any
    unpublish   published            draft       documents.publish     documents.publish_any
    archive     draft or published   archived    documents.publish     documents.edit_any
    restore     archived             draft       documents.publish     documents.edit_any

A user acts only on a document they can see (visibility_clause; otherwise 404). The row is
locked for the transition; the form sends the status it was rendered with, so an action on a
stale page (someone else changed the status meanwhile) is refused instead of applied to a
state the user did not see. Every transition is audited with identifiers and statuses only
(no title, no file name). Publication is allowed in every processing status: the document page
warns when the text is not (fully) searchable.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from baza_wiedzy.core.audit.service import Actor, record
from baza_wiedzy.core.permissions.service import CurrentUser
from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.modules.documents.models import (
    STATUS_ARCHIVED,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    Document,
)
from baza_wiedzy.modules.documents.visibility import visibility_clause

log = logging.getLogger(__name__)

PUBLISH, UNPUBLISH, ARCHIVE, RESTORE = "publish", "unpublish", "archive", "restore"
ACTIONS = (PUBLISH, UNPUBLISH, ARCHIVE, RESTORE)

_TRANSITIONS: dict[str, tuple[tuple[str, ...], str]] = {
    PUBLISH: ((STATUS_DRAFT,), STATUS_PUBLISHED),
    UNPUBLISH: ((STATUS_PUBLISHED,), STATUS_DRAFT),
    ARCHIVE: ((STATUS_DRAFT, STATUS_PUBLISHED), STATUS_ARCHIVED),
    RESTORE: ((STATUS_ARCHIVED,), STATUS_DRAFT),
}
_OTHERS_PERMISSION = {
    PUBLISH: "documents.publish_any",
    UNPUBLISH: "documents.publish_any",
    ARCHIVE: "documents.edit_any",
    RESTORE: "documents.edit_any",
}


class LifecycleError(Exception):
    """message: English gettext id shown to the user (never document data)."""

    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotVisible(LifecycleError):
    status_code = 404


class NotAllowed(LifecycleError):
    status_code = 403


class StaleState(LifecycleError):
    status_code = 409


@dataclass(frozen=True)
class Transition:
    document_id: uuid.UUID
    version_id: uuid.UUID | None
    action: str
    before: str
    after: str


def may(user: CurrentUser, document: Document, action: str) -> bool:
    """Permission part of the table above (visibility is checked by the caller's query)."""
    if action not in _TRANSITIONS:
        return False
    if user.can(_OTHERS_PERMISSION[action]):
        return True
    return document.owner_id == user.id and user.can("documents.publish")


def available_actions(user: CurrentUser, document: Document) -> list[str]:
    """Actions shown on the document page: allowed for this user in the current status."""
    return [
        action
        for action in ACTIONS
        if document.status in _TRANSITIONS[action][0] and may(user, document, action)
    ]


def apply(
    db: Session,
    user: CurrentUser,
    document_id: uuid.UUID,
    action: str,
    *,
    expected_status: str,
    ip: str | None,
) -> Transition:
    """Run one transition in the caller's transaction (the caller commits)."""
    if action not in _TRANSITIONS:
        raise LifecycleError(N_("Unknown action."))
    document = db.scalar(
        select(Document)
        .where(Document.id == document_id, visibility_clause(user))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if document is None:
        raise NotVisible(N_("The document does not exist or you cannot see it."))
    if not may(user, document, action):
        raise NotAllowed(N_("You are not allowed to do this with this document."))
    sources, target = _TRANSITIONS[action]
    if document.status != expected_status or document.status not in sources:
        raise StaleState(
            N_("The status of the document has changed in the meantime. Check it and try again.")
        )
    before = document.status
    now = datetime.now(UTC)
    if action == PUBLISH:
        document.published_version_id = document.latest_version_id
        document.published_at, document.published_by = now, user.id
        document.archived_at = None
    elif action in (UNPUBLISH, RESTORE):
        document.published_version_id = None
        document.published_at = document.published_by = None
        document.archived_at = None
    elif action == ARCHIVE:
        document.archived_at = now
    document.status = target
    document.updated_at = now
    version_id = document.current_version_id()
    record(
        db,
        f"document.{action}",
        Actor(user.id, user.username),
        entity_type="document",
        entity_id=document.id,
        ip=ip,
        details={
            "version_id": str(version_id) if version_id else None,
            "from": before,
            "to": target,
        },
    )
    log.info(
        "document lifecycle: action=%s document=%s from=%s to=%s",
        action,
        document.id,
        before,
        target,
    )
    return Transition(document.id, version_id, action, before, target)
