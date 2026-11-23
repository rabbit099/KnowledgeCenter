"""Pages of the ingestion module: the extracted parts of a version, "Process again", and
"Uruchom OCR" / "Anuluj OCR" (Phase 4 stage B, ADR-033).

All check visibility through documents.services.api (404 for documents the user cannot see,
like the document page). The actions: the owner with documents.upload for own documents,
documents.edit_any for any visible document (403 otherwise); audited as document.reprocess,
document.ocr_request and document.ocr_cancel. Plain POST forms, they work without JavaScript.
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
from baza_wiedzy.modules.ingestion.tasks import enqueue, enqueue_ocr

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


def _back(request: Request, document_id: uuid.UUID, done: str) -> Response:
    target = f"/documents/{document_id}?done={done}"
    if request.headers.get("hx-request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


@router.post("/versions/{version_id}/ocr", include_in_schema=False)
def start_ocr(request: Request, db: Db, user: AnyUser, version_id: str) -> Response:
    """ "Uruchom OCR": a large document on hold, a failed OCR, or scans never started."""
    document, version = _visible(db, user, version_id)
    if not documents.can_reprocess(user, document):
        raise HTTPException(status_code=403)
    settings = request.app.state.settings
    if not settings.ocr_enabled:
        return _back(request, document.id, "ocr_off")
    started = api.request_ocr(
        db, version.id, actor=Actor(user.id, user.username), ip=client_ip(request)
    )
    db.commit()
    if not started:
        return _back(request, document.id, "ocr_unchanged")
    try:
        enqueue_ocr(settings)
    except Exception as exc:  # stays queued; the next worker start sends the message
        log.warning("ocr: enqueue failed: version=%s error=%s", version.id, type(exc).__name__)
    return _back(request, document.id, "ocr_request")


@router.post("/versions/{version_id}/ocr/cancel", include_in_schema=False)
def cancel_ocr(request: Request, db: Db, user: AnyUser, version_id: str) -> Response:
    document, version = _visible(db, user, version_id)
    if not documents.can_reprocess(user, document):
        raise HTTPException(status_code=403)
    cancelled = api.cancel_ocr(
        db, version.id, actor=Actor(user.id, user.username), ip=client_ip(request)
    )
    db.commit()
    return _back(request, document.id, "ocr_cancel" if cancelled else "ocr_unchanged")
