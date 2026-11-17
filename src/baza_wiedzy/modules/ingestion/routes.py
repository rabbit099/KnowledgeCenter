"""Pages of the ingestion module: the extracted parts of a version and "Process again".

Both check visibility through documents.services.api (404 for documents the user cannot see,
like the document page). Reprocessing: the owner with documents.upload for own documents,
documents.edit_any for any visible document (403 otherwise); audited as document.reprocess.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from baza_wiedzy.core.audit.service import Actor
from baza_wiedzy.core.auth.deps import Db, require_user
from baza_wiedzy.core.auth.routes import client_ip
from baza_wiedzy.core.permissions.service import CurrentUser
from baza_wiedzy.modules.documents.services import api as documents
from baza_wiedzy.modules.ingestion.services import api
from baza_wiedzy.modules.ingestion.tasks import enqueue

log = logging.getLogger(__name__)
router = APIRouter(prefix="/ingestion")
AnyUser = Annotated[CurrentUser, Depends(require_user)]
PREVIEW_CHARS = 5000


def _visible(db: Db, user: CurrentUser, version_id: str) -> tuple[Any, Any]:
    try:
        key = uuid.UUID(version_id)
    except ValueError:
        raise HTTPException(status_code=404) from None
    found = documents.get_visible_version(db, user, key)
    if found is None:
        raise HTTPException(status_code=404)
    return found


@router.get("/versions/{version_id}/parts", include_in_schema=False)
def version_parts(request: Request, db: Db, user: AnyUser, version_id: str) -> Response:
    document, version = _visible(db, user, version_id)
    templates: Jinja2Templates = request.app.state.templates
    context = {
        "document": document,
        "version": version,
        "parts": api.parts_of(db, version.id),
        "notes": api.notes_of(db, version.id),
        "email": documents.email_metadata_of(db, version.id),
        "can_reprocess": documents.can_reprocess(user, document),
        "preview_chars": PREVIEW_CHARS,
    }
    return templates.TemplateResponse(request, "ingestion/_parts.html", context)


@router.post("/versions/{version_id}/reprocess", include_in_schema=False)
def reprocess(request: Request, db: Db, user: AnyUser, version_id: str) -> Response:
    document, version = _visible(db, user, version_id)
    if not documents.can_reprocess(user, document):
        raise HTTPException(status_code=403)
    settings = request.app.state.settings
    done = "reprocess"
    try:
        api.request_reprocess(
            db,
            settings,
            version.id,
            actor=Actor(user.id, user.username),
            ip=client_ip(request),
        )
        db.commit()
    except api.ReprocessBusy:
        db.rollback()
        done = "busy"
    else:
        try:
            enqueue(version.id, settings)
        except Exception as exc:  # stays pending; the worker start enqueues it
            log.warning(
                "ingestion: enqueue failed: version=%s error=%s", version.id, type(exc).__name__
            )
    target = f"/documents/{document.id}?done={done}"
    if request.headers.get("hx-request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)
