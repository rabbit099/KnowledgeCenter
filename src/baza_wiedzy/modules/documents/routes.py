"""Pages of the documents module: my documents, upload, document details, lifecycle actions
(publish, unpublish, archive, restore; ordinary POST forms, work without JavaScript) and the
download of the original (ADR-025).

Upload takes several files at once and works without JavaScript too (plain multipart POST; the
CSRF token is the first form field, see core/auth/csrf.py); HTMX and a small script (drag and
drop, limits checked before sending) only improve it. Results per file: batches.py, ADR-032.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from baza_wiedzy.core.audit.service import Actor
from baza_wiedzy.core.auth.deps import Db, require_any_permission, require_permission, require_user
from baza_wiedzy.core.auth.routes import client_ip
from baza_wiedzy.core.events import DOCUMENT_PUBLISHED, DOCUMENT_VERSION_UPLOADED
from baza_wiedzy.core.permissions.service import CurrentUser
from baza_wiedzy.core.web.i18n import N_
from baza_wiedzy.modules.documents import batches, download, filenames, lifecycle, upload
from baza_wiedzy.modules.documents.filetypes import UnsupportedFile
from baza_wiedzy.modules.documents.models import DONE, PENDING, PROCESSING
from baza_wiedzy.modules.documents.services import api
from baza_wiedzy.modules.documents.storage import get_storage

router = APIRouter(prefix="/documents")

Uploader = Annotated[CurrentUser, Depends(require_permission("documents.upload"))]
Lister = Annotated[
    CurrentUser, Depends(require_any_permission("documents.upload", "documents.read_drafts"))
]
AnyUser = Annotated[CurrentUser, Depends(require_user)]
MB = 1024 * 1024
MULTIPART_OVERHEAD = 64 * 1024  # headers of the multipart body around the files
EXTRA_FILES = 50  # files beyond UPLOAD_MAX_FILES still parsed, only to be listed as refused
TOTAL_TOO_LARGE = N_(
    "The files are together larger than the allowed size (%(mb)s MB). Upload them in several parts."
)
TOO_MANY_FILES = N_("Too many files in one upload: at most %(n)s. Upload them in several parts.")
MORE_THAN_LIMIT = N_("More files than one upload accepts (%(n)s); upload this one again.")
SAVE_FAILED = N_("The file could not be saved. Try again.")
log = logging.getLogger(__name__)


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
    request: Request,
    db: Db,
    user: Lister,
    q: str = "",
    scope: str = "own",
    page: str = "1",
    author: str = "",
    batch: str = "",
) -> Response:
    scope = "all" if scope == "all" and user.can("documents.read_drafts") else "own"
    show_authors = api.can_see_authors(user)
    owner_id = None
    if show_authors and author:  # ignored for everyone else (no hint that the account exists)
        try:
            owner_id = uuid.UUID(author)
        except ValueError:
            owner_id = None
    result = api.list_documents(
        db, user, q=q.strip(), scope=scope, page=_page(page), owner_id=owner_id
    )
    busy = any(_busy(row[1]) for row in result.items)
    filters = {"q": q, "scope": "all" if owner_id else scope}
    if owner_id:
        filters["author"] = str(owner_id)
    context = {
        "result": result,
        "filters": filters,
        "busy": busy,
        "show_authors": show_authors,
        "owners": api.visible_owners(db, user) if show_authors else [],
        "batch": _batch_view(request, db, user, batch) if batch else None,
    }
    partial = request.headers.get("hx-request") == "true"
    template = "documents/_table.html" if partial else "documents/list.html"
    return _render(request, template, context)


def _upload_context(settings: Any, **extra: Any) -> dict[str, Any]:
    return {
        "max_mb": settings.max_upload_mb,
        "max_files": settings.upload_max_files,
        "max_total_mb": settings.upload_total_mb,
        **extra,
    }


def _batch_view(
    request: Request, db: Db, user: CurrentUser, batch_id: str
) -> dict[str, Any] | None:
    """Rows of the user's own upload batch with the counts, or None (unknown or someone else's)."""
    store = batches.BatchStore(request.app.state.settings.storage_dir)
    rows = store.load(batch_id, user.id)
    if rows is None:
        return None
    for row in rows:
        if row["status"] == batches.HELD:
            row["duplicates"] = api.visible_duplicates(db, user, row["sha256"])
    counts = {
        status: sum(1 for row in rows if row["status"] == status)
        for status in (batches.UPLOADED, batches.HELD, batches.SKIPPED, batches.REJECTED)
    }
    hours = request.app.state.settings.upload_batch_hours
    return {"id": batch_id, "rows": rows, "counts": counts, "hours": hours}


@router.get("/upload", include_in_schema=False)
def upload_form(request: Request, user: Uploader) -> Response:
    context = _upload_context(request.app.state.settings)
    return _render(request, "documents/upload.html", context)


async def _chunks(file: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await file.read(upload.CHUNK):
        yield chunk


def _redirect(request: Request, target: str) -> Response:
    if request.headers.get("hx-request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


def _rejected(name: str, reason: str, *, too_large: bool = False, **values: Any) -> dict[str, Any]:
    return {
        "name": name,
        "status": batches.REJECTED,
        "reason": reason,
        "values": values,
        "too_large": too_large,
    }


@router.post("/upload", include_in_schema=False)
async def upload_file(request: Request, db: Db, user: Uploader) -> Response:
    """One or more files (field "files"; "file" still accepted). Each file is checked, stored,
    audited and announced on its own: a refused file never stops the others (ADR-032)."""
    settings = request.app.state.settings
    max_bytes = settings.max_upload_mb * MB
    limit = settings.upload_max_files

    def error(message: str, status: int, **values: Any) -> Response:
        context = _upload_context(settings, error=message, error_values=values)
        return _render(request, "documents/_upload_form.html", context, status)

    declared = request.headers.get("content-length")
    total_limit = settings.upload_total_mb * MB + MULTIPART_OVERHEAD
    if declared and declared.isdigit() and int(declared) > total_limit:
        return error(TOTAL_TOO_LARGE, 413, mb=settings.upload_total_mb)
    storage = get_storage(request.app.state)
    storage.cleanup_incoming()  # orphans of interrupted uploads (older than an hour)
    store = batches.BatchStore(settings.storage_dir)
    store.cleanup(settings.upload_batch_hours * 3600)
    try:
        form = await request.form(max_files=limit + EXTRA_FILES, max_fields=10)
    except StarletteHTTPException:
        return error(TOO_MANY_FILES, 413, n=limit)
    files = [
        item
        for field in ("files", "file")
        for item in form.getlist(field)
        if isinstance(item, UploadFile) and item.filename
    ]
    if not files:
        return error(N_("Choose a file to upload."), 422)
    title = str(form.get("title") or "") if len(files) == 1 else ""
    actor = Actor(user.id, user.username)
    batch_id = store.new_id()
    rows: list[dict[str, Any]] = []
    try:
        for index, file in enumerate(files):
            raw_name = file.filename or ""
            if index >= limit:
                rows.append(_rejected(filenames.sanitize(raw_name), MORE_THAN_LIMIT, n=limit))
                continue
            rows.append(
                await _upload_one(
                    request, db, user, actor, store, batch_id, index, file, max_bytes, title
                )
            )
    finally:
        for file in files:
            await file.close()
    if len(rows) == 1 and rows[0]["status"] == batches.UPLOADED:
        return _redirect(request, f"/documents/{rows[0]['document_id']}?done=uploaded")
    if len(rows) == 1 and rows[0]["status"] == batches.REJECTED:
        status = 413 if rows[0]["too_large"] else 422
        return error(rows[0]["reason"], status, **rows[0]["values"])
    store.save(batch_id, user.id, rows)
    return _redirect(request, f"/documents?batch={batch_id}")


async def _upload_one(
    request: Request,
    db: Db,
    user: CurrentUser,
    actor: Actor,
    store: batches.BatchStore,
    batch_id: str,
    index: int,
    file: UploadFile,
    max_bytes: int,
    title: str,
) -> dict[str, Any]:
    raw_name = file.filename or ""
    storage = get_storage(request.app.state)
    received: upload.Received | None = None
    try:
        received = await upload.receive(_chunks(file), storage, max_bytes)

        def save() -> dict[str, Any]:
            filename, _kind = upload.check(received, raw_name)
            if api.visible_duplicates(db, user, received.sha256):
                os.replace(received.path, store.held_path(batch_id, index))
                return {
                    "name": filename,
                    "status": batches.HELD,
                    "sha256": received.sha256,
                    "size": received.size,
                    "title": title,
                }
            done = upload.create(
                db,
                storage,
                received,
                actor=actor,
                raw_filename=raw_name,
                title=title,
                ip=client_ip(request),
            )
            db.commit()
            return {
                "name": filename,
                "status": batches.UPLOADED,
                "document_id": str(done.document.id),
                "version_id": str(done.version.id),
            }

        row = await run_in_threadpool(save)
    except upload.UploadTooLarge as exc:
        return _rejected(filenames.sanitize(raw_name), exc.message, too_large=True)
    except UnsupportedFile as exc:
        return _rejected(filenames.sanitize(raw_name), exc.message)
    except Exception as exc:  # one broken file never stops the others
        if received is not None:
            received.path.unlink(missing_ok=True)  # not yet stored: nothing else refers to it
        await run_in_threadpool(db.rollback)
        log.error("upload of one file failed: %s", type(exc).__name__)
        return _rejected(filenames.sanitize(raw_name), SAVE_FAILED)
    if row["status"] == batches.UPLOADED:
        _announce(request, row)
    return row


def _announce(request: Request, row: dict[str, Any]) -> None:
    # After the commit: ingestion (if enabled) enqueues processing. If this is lost (Redis
    # down), the version stays "pending" and the worker re-enqueues it at start.
    request.app.state.events.publish(
        DOCUMENT_VERSION_UPLOADED,
        {"document_id": row["document_id"], "version_id": row["version_id"]},
    )


@router.post("/upload/batches/{batch_id}/{row}", include_in_schema=False)
def decide_duplicate(
    request: Request,
    db: Db,
    user: Uploader,
    batch_id: str,
    row: int,
    decision: Annotated[str, Form()] = "",
) -> Response:
    """ "Wgraj mimo to" (keep) or "Pomiń" (skip) for a file held as a possible duplicate."""
    if decision not in ("keep", "skip"):
        raise HTTPException(status_code=422)
    settings = request.app.state.settings
    store = batches.BatchStore(settings.storage_dir)
    uploaded: dict[str, Any] | None = None
    with store.locked(batch_id, user.id) as found:
        if found is None:
            raise HTTPException(status_code=404)
        rows, save = found
        if not 0 <= row < len(rows):
            raise HTTPException(status_code=404)
        entry = rows[row]
        if entry["status"] == batches.HELD:  # otherwise decided already (another tab): no-op
            held = store.held_path(batch_id, row)
            if decision == "skip" or not held.exists():
                held.unlink(missing_ok=True)
                entry["status"] = batches.SKIPPED
            else:
                received = upload.Received(path=held, size=entry["size"], sha256=entry["sha256"])
                try:
                    done = upload.create(
                        db,
                        get_storage(request.app.state),
                        received,
                        actor=Actor(user.id, user.username),
                        raw_filename=entry["name"],
                        title=entry.get("title", ""),
                        ip=client_ip(request),
                    )
                    db.commit()
                except UnsupportedFile as exc:
                    entry.update(_rejected(entry["name"], exc.message))
                else:
                    entry.update(
                        status=batches.UPLOADED,
                        document_id=str(done.document.id),
                        version_id=str(done.version.id),
                    )
                    uploaded = entry
            save()
    if uploaded is not None:
        _announce(request, uploaded)
    return RedirectResponse(f"/documents?batch={batch_id}", status_code=303)


def _busy(version: api.Version) -> bool:
    """The page polls while the text pass or OCR is under way."""
    return version.processing_status in (PENDING, PROCESSING) or version.ocr_status in (
        api.OCR_QUEUED,
        api.OCR_RUNNING,
    )


def _ocr_context(request: Request, user: CurrentUser, document: api.Document) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "can_act": api.can_reprocess(user, document),
        "ocr_enabled": settings.ocr_enabled,
        "ocr_auto_max_pages": settings.ocr_auto_max_pages,
        "ocr_max_pages": settings.ocr_max_pages,
    }


def _document_key(document_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=404) from None


def _visible(db: Db, user: CurrentUser, document_id: str) -> api.Document:
    key = _document_key(document_id)
    document = api.get_visible_document(db, user, key)
    if document is None:
        # 404, not 403: the existence of other people's drafts is not revealed.
        raise HTTPException(status_code=404)
    return document


def _detail(
    request: Request,
    db: Db,
    user: CurrentUser,
    document: api.Document,
    *,
    error: str | None = None,
    status: int = 200,
) -> Response:
    versions = api.versions_of(db, document)
    latest = versions[0]
    context = {
        "document": document,
        "version": latest,
        "duplicates": api.visible_duplicates(db, user, latest.sha256, exclude_version_id=latest.id),
        "done": request.query_params.get("done"),
        "busy": _busy(latest),
        **_ocr_context(request, user, document),
        # The text is not (fully) searchable unless processing finished with every page read.
        "not_searchable": latest.processing_status != DONE,
        "actions": lifecycle.available_actions(user, document),
        "error": error,
    }
    return _render(request, "documents/detail.html", context, status)


@router.get("/{document_id}", include_in_schema=False)
def document_detail(request: Request, db: Db, user: AnyUser, document_id: str) -> Response:
    return _detail(request, db, user, _visible(db, user, document_id))


@router.post("/{document_id}/lifecycle", include_in_schema=False)
def document_lifecycle(
    request: Request,
    db: Db,
    user: AnyUser,
    document_id: str,
    action: Annotated[str, Form(max_length=20)] = "",
    expected: Annotated[str, Form(max_length=20)] = "",
) -> Response:
    """Publish, unpublish, archive or restore (lifecycle.py has the permission table)."""
    key = _document_key(document_id)
    try:
        transition = lifecycle.apply(
            db, user, key, action, expected_status=expected, ip=client_ip(request)
        )
        db.commit()
    except lifecycle.NotVisible:
        db.rollback()
        raise HTTPException(status_code=404) from None
    except lifecycle.LifecycleError as exc:
        db.rollback()
        document = _visible(db, user, document_id)
        return _detail(request, db, user, document, error=exc.message, status=exc.status_code)
    if transition.action == lifecycle.PUBLISH and transition.version_id is not None:
        request.app.state.events.publish(
            DOCUMENT_PUBLISHED,
            {"document_id": str(transition.document_id), "version_id": str(transition.version_id)},
        )
    return RedirectResponse(f"/documents/{key}?done={transition.action}", status_code=303)


@router.get("/{document_id}/download", include_in_schema=False)
def document_download(request: Request, db: Db, user: AnyUser, document_id: str) -> Response:
    key = _document_key(document_id)
    try:
        prepared = download.prepare(
            db, user, key, storage_dir=request.app.state.settings.storage_dir, ip=client_ip(request)
        )
    except download.OriginalMissing:
        raise HTTPException(status_code=500) from None
    if prepared is None:
        raise HTTPException(status_code=404)
    db.commit()  # the audit entry exists before the first byte is sent
    headers = dict(prepared.headers, **{"content-length": str(prepared.size)})
    return StreamingResponse(
        download.read_chunks(prepared.path),
        media_type="application/octet-stream",
        headers=headers,
    )


@router.get("/{document_id}/status", include_in_schema=False)
def document_status(request: Request, db: Db, user: AnyUser, document_id: str) -> Response:
    document = _visible(db, user, document_id)
    latest = api.versions_of(db, document)[0]
    context = {
        "document": document,
        "version": latest,
        "busy": _busy(latest),
        "polled": request.headers.get("hx-request") == "true",
        **_ocr_context(request, user, document),
    }
    return _render(request, "documents/_status.html", context)
