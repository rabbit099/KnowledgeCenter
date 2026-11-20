"""Which documents a user may see, as an SQL condition (CLAUDE.md 6: visibility is enforced in
the SQL of every query, never by filtering results afterwards).

    any status  -> the owner (own documents in every state, archived included)
    draft       -> holders of documents.read_drafts
    published   -> holders of documents.read
    archived    -> holders of documents.edit_any

documents.publish_any and documents.edit_any do not show other people's drafts by themselves:
the role editor requires documents.read_drafts next to them (Permission.requires, ADR-025).
Readers never see archived documents of others.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, or_

from baza_wiedzy.core.permissions.service import CurrentUser
from baza_wiedzy.modules.documents.models import (
    STATUS_ARCHIVED,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    Document,
)


def visibility_clause(user: CurrentUser) -> ColumnElement[bool]:
    """SQL condition on doc_documents: which documents this user may see."""
    rules: list[ColumnElement[bool]] = [Document.owner_id == user.id]
    if user.can("documents.read_drafts"):
        rules.append(Document.status == STATUS_DRAFT)
    if user.can("documents.read"):
        rules.append(Document.status == STATUS_PUBLISHED)
    if user.can("documents.edit_any"):
        rules.append(Document.status == STATUS_ARCHIVED)
    return or_(*rules)
