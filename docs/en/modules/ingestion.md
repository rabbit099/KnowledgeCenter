# Module: ingestion

> Applies to: v0.2.1 · Last reviewed: 2026-11-17

## Scope

Turns a stored file into text parts in the `worker`: runs the extractor for the version's kind (TXT, EML, DOCX, PDF, DOC, MSG; native code in child processes; OCR in Phase 4), processes e-mail attachments, chunks long text, detects the language per part, enforces the safety limits, keeps the processing status moving (retries, stuck recovery) and publishes `document.version.extracted` through its outbox. Details: [ingestion-and-ocr.md](../ingestion-and-ocr.md).

## Dependencies

Requires [documents](documents.md) (status, e-mail metadata, visibility, file names and type checks through `documents.services.api`). Parser libraries (`python-docx`, `pypdfium2`, `extract-msg`, `lingua-language-detector`, OCRmyPDF) and LibreOffice come with the `worker` extra and exist only in the worker image; the web application imports only the light parts (routes, `tasks.enqueue`, `services/api.py`), which a unit test checks.

## Permissions

None of its own. "Przetwórz ponownie" (`POST /ingestion/versions/<id>/reprocess`) is allowed for the owner with `documents.upload` (own documents) and for `documents.edit_any` (any document), and only for documents the user can see (404 otherwise, 403 when visible without the right). Page of parts: `GET /ingestion/versions/<id>/parts` (HTMX fragment on the document page), anyone who can see the document. Audit: `document.reprocess` (document id, `version_id`, `sha256`).

## Tables

Branch `ingestion` (`ingestion_0001`, `depends_on` `documents_0002`):
- `ing_parts`: extracted text, one row per chunk (ADR-020); `version_id` → `doc_versions` with `ON DELETE CASCADE`.
- `ing_results`: per version the notes (detected encoding, skipped attachments, fallback reader), number of parts and characters.
- `ing_outbox`: durable `document.version.extracted` events.

Columns: [data-model.md](../data-model.md#ingestion).

## Events

- Subscribes to `document.version.uploaded` (in `app`): enqueues `ingestion.process_version`. If Redis is down the version stays pending and the worker start enqueues it.
- Publishes `document.version.extracted` (`document_id`, `version_id`; no text) through the outbox, at least once, after the parts are committed. Subscribers read the parts with `parts_of` and must be idempotent.

## Public interface

`modules/ingestion/services/api.py`:
- `parts_of(db, version_id)` → list of `PartView` (seq, part type, origin, page, chunk number, language, text, size, OCR flag) — the source for the search index (Phase 3).
- `notes_of(db, version_id)`, `extracted_version_ids(db)` (full reindex).
- `request_reprocess(db, settings, version_id, actor=…, ip=…)`: back to pending with a fresh attempt counter and audit; the caller checks permissions, commits and enqueues; raises `ReprocessBusy` while an attempt runs.

Worker start hook (manifest `worker_startup`): `startup.on_worker_start` (temporary files, stuck and pending versions, outbox). CLI: `baza-wiedzy reprocess`, `baza-wiedzy events redeliver` ([deployment-operations.md](../deployment-operations.md)).

## Extension points

- Extractor registry (`extractors/__init__.py`): a new format is a new file with `extract(path, ctx)` and one registry line. `ctx.add_text()`, `ctx.note()` and `ctx.attachment()` give limits, origins and recursion for free.
- Virus scan hook (`scanning.py`, `SCAN_HOOK`).

## Status

Phase 2, stages B and C: pipeline (claim, scan hook, extract, chunk, language, one-transaction store, outbox), statuses with retries for transient errors and stuck recovery, extractors for TXT, EML, DOCX, PDF (child process, scanned pages marked "wymaga OCR"), DOC (LibreOffice in a child process) and MSG, limits against archive and XML bombs, parts page and "Przetwórz ponownie" (UI and CLI), the e-mail subject as default title. Stage D: worker without internet access; Phase 4: OCR.
