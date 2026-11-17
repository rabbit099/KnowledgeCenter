"""Pages of the documents module: my documents, upload, document details.

Upload works only with JavaScript (HTMX sends the CSRF token in a header; multipart bodies are
not read by the CSRF middleware); without JavaScript the page says so in <noscript>.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from baza_wiedzy.core.audit.service import Actor
from baza_wiedzy.core.auth.deps import Db, require_any_permission, require_permission, require_user
from baza_wiedzy.core.auth.routes import client_ip
from baza_wiedzy.core.events import DOCUMENT_VERSION_UPLOADED
from baza_wiedzy.core.permissions.service import CurrentUser
from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.modules.documents import upload
from baza_wiedzy.modules.documents.filetypes import UnsupportedFile
from baza_wiedzy.modules.documents.models import PENDING, PROCESSING
from baza_wiedzy.modules.documents.services import api
from baza_wiedzy.modules.documents.storage import get_storage

router = APIRouter(prefix="/documents")

Uploader = Annotated[CurrentUser, Depends(require_permission("documents.upload"))]
Lister = Annotated[
    CurrentUser, Depends(require_any_permission("documents.upload", "documents.read_drafts"))
]
AnyUser = Annotated[CurrentUser, Depends(require_user)]
MULTIPART_OVERHEAD = 64 * 1024  # headers of the multipart body around the file


def _render(
    request: Request, template: str, context: dict[str, Any], status: int = 200
) -> Response:
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(request, template, context, status_code=status)


def _page(value: str) -> int:
    try:
        return max(1, int(value))
    except ValueError:
        return 1


@router.get("", include_in_schema=False)
def my_documents(
    request: Request, db: Db, user: Lister, q: str = "", scope: str = "own", page: str = "1"
) -> Response:
    scope = "all" if scope == "all" and user.can("documents.read_drafts") else "own"
    result = api.list_documents(db, user, q=q.strip(), scope=scope, page=_page(page))
    busy = any(version.processing_status in (PENDING, PROCESSING) for _, version in result.items)
    context = {"result": result, "filters": {"q": q, "scope": scope}, "busy": busy}
    partial = request.headers.get("hx-request") == "true"
    template = "documents/_table.html" if partial else "documents/list.html"
    return _render(request, template, context)


@router.get("/upload", include_in_schema=False)
def upload_form(request: Request, user: Uploader) -> Response:
    return _render(
        request, "documents/upload.html", {"max_mb": request.app.state.settings.max_upload_mb}
    )


async def _chunks(file: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await file.read(upload.CHUNK):
        yield chunk


@router.post("/upload", include_in_schema=False)
async def upload_file(request: Request, db: Db, user: Uploader) -> Response:
    settings = request.app.state.settings
    max_bytes = settings.max_upload_mb * 1024 * 1024

    def error(message: str, status: int) -> Response:
        context = {"error": message, "max_mb": settings.max_upload_mb}
        return _render(request, "documents/_upload_form.html", context, status)

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes + MULTIPART_OVERHEAD:
        return error(N_("The file is larger than the allowed size."), 413)
    storage = get_storage(request.app.state)
    storage.cleanup_incoming()  # orphans of interrupted uploads (older than an hour)
    form = await request.form(max_files=1, max_fields=5)
    file = form.get("file")
    if not isinstance(file, UploadFile) or not file.filename:
        return error(N_("Choose a file to upload."), 422)
    try:
        received = await upload.receive(_chunks(file), storage, max_bytes)
        title = str(form.get("title") or "")
        actor = Actor(user.id, user.username)

        def save() -> upload.Uploaded:
            done = upload.create(
                db,
                storage,
                received,
                actor=actor,
                raw_filename=file.filename or "",
                title=title,
                ip=client_ip(request),
            )
            db.commit()
            return done

        result = await run_in_threadpool(save)
    except upload.UploadTooLarge as exc:
        return error(exc.message, 413)
    except UnsupportedFile as exc:
        return error(exc.message, 422)
    finally:
        await file.close()
    # After the commit: ingestion (if enabled) enqueues processing. If this is lost (Redis
    # down), the version stays "pending" and the worker re-enqueues it at start.
    request.app.state.events.publish(
        DOCUMENT_VERSION_UPLOADED,
        {"document_id": str(result.document.id), "version_id": str(result.version.id)},
    )
    target = f"/documents/{result.document.id}?done=uploaded"
    if request.headers.get("hx-request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


def _visible(db: Db, user: CurrentUser, document_id: str) -> api.Document:
    try:
        key = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=404) from None
    document = api.get_visible_document(db, user, key)
    if document is None:
        # 404, not 403: the existence of other people's drafts is not revealed.
        raise HTTPException(status_code=404)
    return document


@router.get("/{document_id}", include_in_schema=False)
def document_detail(request: Request, db: Db, user: AnyUser, document_id: str) -> Response:
    document = _visible(db, user, document_id)
    versions = api.versions_of(db, document)
    latest = versions[0]
    context = {
        "document": document,
        "version": latest,
        "duplicates": api.visible_duplicates(db, user, latest.sha256, exclude_version_id=latest.id),
        "done": request.query_params.get("done"),
        "busy": latest.processing_status in (PENDING, PROCESSING),
    }
    return _render(request, "documents/detail.html", context)


@router.get("/{document_id}/status", include_in_schema=False)
def document_status(request: Request, db: Db, user: AnyUser, document_id: str) -> Response:
    document = _visible(db, user, document_id)
    latest = api.versions_of(db, document)[0]
    context = {
        "document": document,
        "version": latest,
        "busy": latest.processing_status in (PENDING, PROCESSING),
    }
    return _render(request, "documents/_status.html", context)
