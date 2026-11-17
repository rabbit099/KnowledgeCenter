# Ingestion and OCR

> Applies to: v0.2.1 · Last reviewed: 2026-11-17

How an uploaded file becomes searchable text. Upload validation and storage belong to the `documents` module; the processing described here belongs to the `ingestion` module (see [modules/ingestion.md](modules/ingestion.md)).

## Supported formats

| Format | Extractor | Status |
|---|---|---|
| TXT | own encoding rule: UTF-8, Windows-1250, ISO-8859-2 ([Text encodings](#text-encodings), ADR-022) | Phase 2 stage B |
| EML | stdlib `email` (policy=default): headers, text body (HTML body converted to text), attachments | Phase 2 stage B |
| DOCX | python-docx (paragraphs and tables in order, headers, footers) + hardened raw-XML fallback | Phase 2 stage B |
| PDF with text | pypdfium2, one part per page | Phase 2 stage C |
| DOC | LibreOffice headless conversion to text (isolated profile, timeout) | Phase 2 stage C |
| MSG | `extract-msg` | Phase 2 stage C |
| PDF scanned | OCRmyPDF + Tesseract (`pol+eng`); pages without text are only marked "needs OCR" until then | Phase 4 |

Attachments of EML/MSG that are themselves supported files are extracted recursively (depth ≤ `INGEST_MAX_DEPTH`, default 3) and stored as parts of type `attachment` in `ing_parts`. Other attachments (images, archives) are skipped with a note on the document page. OCR of standalone images is out of scope for the MVP.

## Upload validation (in `app`)

Page "Moje dokumenty" → "Wgraj dokument" (`/documents/upload`, permission `documents.upload`). Uploading needs JavaScript: HTMX sends the CSRF token in the `X-CSRF-Token` header (the CSRF middleware does not read multipart bodies); without JavaScript the page shows a `<noscript>` notice.

1. Size: the request is refused early when `Content-Length` exceeds `MAX_UPLOAD_MB` (default 50), and the copy stops as soon as the real size passes it (HTTP 413, "Plik jest większy niż dozwolony rozmiar."). The proxy refuses bodies above `MAX_REQUEST_MB` (default 51: the file plus multipart overhead); keep it one MB above `MAX_UPLOAD_MB`.
2. The body is parsed by Starlette (at most 1 MB in memory, the rest in a temporary spool file, never the whole file in memory); the file is copied in 1 MB chunks to `/data/storage/.incoming/` while SHA-256 and the size are computed.
3. Type **by content**, never by extension or `Content-Type` (ADR-021): the extension must be in the allow-list (pdf, docx, doc, txt, eml, msg) **and** match the detected type:

   | Type | Check |
   |---|---|
   | PDF | `%PDF-` within the first 1024 bytes |
   | DOCX | ZIP whose central directory contains `[Content_Types].xml` and `word/document.xml` (a renamed `.xlsx` is refused) |
   | DOC | OLE2 compound file with the stream `WordDocument` |
   | MSG | OLE2 compound file with the stream `__properties_version1.0` |
   | EML | RFC 5322 header block with `From` and at least one more mail header, no NUL bytes |
   | TXT | no NUL bytes in the first 64 KB (UTF-16 with BOM allowed) |

   Any other OLE2 file (for example an old `.xls` renamed to `.doc`) and any mismatch are refused (HTTP 422, "Zawartość pliku nie pasuje do jego rozszerzenia albo plik jest uszkodzony."); the incoming file is deleted.
4. The file is moved with an atomic rename to `originals/<aa>/<bb>/<key>` (key = random UUID) and made read-only. The user's file name is sanitised (directory parts, control and bidi characters removed, NFC, at most 255 characters) and kept only as metadata.
5. A draft document with version 1 is created (`doc_documents`, `doc_versions`, `processing_status = pending`); title:
   - the "Tytuł" field when filled in: exactly as typed, only runs of spaces collapsed and at most 300 characters;
   - otherwise the file name without extension (`Umowa_SERWIS-2026.pdf` → `Umowa_SERWIS-2026`);
   - for EML and MSG without a typed title, processing replaces it with the subject of the message (same cleaning); until then, or when the subject is empty, the file name stays.

   Upper and lower case are never changed anywhere: the title shows the case of what was typed, of the file name or of the subject. The origin is kept in `doc_documents.title_source` (`user`, `filename`, `subject`), so a typed title is never replaced.
6. Duplicates: the document page shows "Możliwy duplikat" with links when another document **visible to the uploader** has a version with the same SHA-256. Other people's drafts are never compared, so the warning reveals nothing. The upload is not blocked; removing a duplicate comes with the lifecycle (Phase 5).
7. Audit `document.upload` with the document id, `version_id`, `sha256`, `size_bytes` and `kind` (no file name, no content). After the commit the event `document.version.uploaded` is published and the `ingestion` module enqueues processing.
8. Virus scan: a step of the processing pipeline, disabled by default ([Virus scan hook](#virus-scan-hook)); ClamAV is not wired in Phase 2.

Drafts are visible only to the uploader and to holders of `documents.read_drafts` (Admin); anyone else gets **404** for the document page, so its existence is not revealed.

## Processing (in `worker`)

The upload publishes `document.version.uploaded`; the `ingestion` module enqueues the Dramatiq job `ingestion.process_version` (queue `ingestion`). If Redis is down at that moment the version simply stays "oczekuje" and the next worker start enqueues it.

Status of a version (`doc_versions.processing_status`, owned by `documents`, changed only through `documents.services.api`; UI labels in brackets):

```
pending (oczekuje) --claim--> processing (przetwarzanie) --> done (gotowe) | needs_ocr (wymaga OCR)
                                         |--> failed (błąd)                 permanent error, no retry
pending <---- retry with a delay --------|                                  transient error
processing for longer than INGEST_STALE_MINUTES --> pending, or failed after the last attempt
done | failed | needs_ocr --"Przetwórz ponownie"--> pending (attempt counter reset)
```

One attempt (`ingestion/pipeline.py`):
1. **Claim** the version under a row lock: only `pending` (or a stale `processing`) becomes `processing`, the attempt counter grows by one. A duplicate job or a version that is already done does nothing.
2. **Virus scan hook** (`SCAN_HOOK`, default `none`, see below).
3. **Extract** the original (read-only) in a private temporary directory `/data/storage/.work/<id>-…` on the storage volume; the directory is removed afterwards.
4. **Chunk**: texts longer than 100,000 characters are split at a paragraph break, else a line break, else a space (`chunk_no` 1, 2, …), so each tsvector stays far below the 1 MB limit.
5. **Language** of each chunk.
6. **Store in one transaction**: delete the old parts of the version, write the new parts (`ing_parts`) and notes (`ing_results`), the e-mail headers (`doc_email_metadata`, through `documents.services.api`), the final status and an outbox entry. Processing a version twice therefore never duplicates parts. If the version was reprocessed or recovered while this attempt ran, its result is discarded.
7. **Deliver** the outbox entry: `document.version.extracted` (`document_id`, `version_id`, nothing else) is published on the event bus.

The document page polls the status every 3 s while it is "oczekuje" or "przetwarzanie" and then shows the e-mail headers, notes and the extracted parts (origin, language, size, a preview of the first 5,000 characters).

### Errors and retries

| Kind | Examples | Result |
|---|---|---|
| Permanent | damaged file, unrecognised text encoding, archive bomb, more text or parts than allowed, kind not processed yet | `failed` at once with a readable message, no retry |
| Transient | database or Redis unavailable, time limit `INGEST_TIME_LIMIT_SECONDS` (900 s) passed, external process killed | back to `pending` ("Chwilowy problem; przetwarzanie zostanie automatycznie ponowione.") and enqueued again after `INGEST_RETRY_MIN_SECONDS × 2^(attempt−1)`, at most `INGEST_RETRY_MAX_SECONDS` (30 s, 60 s, 120 s, … 600 s); `failed` after `INGEST_MAX_ATTEMPTS` (3) attempts |
| Unexpected (a bug) | a parser crashes | `failed` with "błąd wewnętrzny"; the log has the exception class and the stack frames |

An attachment that is damaged or unsupported does not fail the e-mail: it is skipped with a note. Limits (text, parts) always fail the whole version.

User messages name only the kind of problem. Logs contain identifiers, kinds, exception classes, counts and stack frames (file, line, function) — never document text, file names, e-mail headers or exception messages, which could quote content. The audit log records `document.reprocess` with the document id, `version_id` and `sha256`.

### Text encodings

TXT files and text attachments are decoded by a fixed rule (ADR-022):
1. A byte order mark decides (UTF-8, UTF-16).
2. Valid UTF-8 (pure ASCII included) is UTF-8.
3. Otherwise Windows-1250 and ISO-8859-2 are tried. Bytes 0x80–0x9F rule out ISO-8859-2 (they are control characters there). The candidate with more Polish letters wins; a tie goes to Windows-1250.
4. Confidence check: at least 80% of the non-ASCII characters of the result must be Polish letters or common typographic signs (quotes, dashes, `…`, `€`, `§`, no-break space, …).

If the check fails (for example the DOS code page CP852, or binary data), the version fails with "Nie rozpoznano kodowania tekstu (obsługiwane: UTF-8, Windows-1250, ISO-8859-2). Zapisz plik w UTF-8 i wgraj go ponownie." There is deliberately no guess: a wrong guess would index garbage. The detected encoding is shown as a note on the document page.

### E-mail

- **Body**: `text/plain` is preferred; an HTML-only body is converted to text with the standard library parser — nothing is executed or fetched, scripts and styles are dropped, links keep their visible text only.
- **Headers** of the top-level message (subject, from, to, cc, date, Message-ID) go to `doc_email_metadata` through `documents.services.api` and are shown on the document page; they are never logged. A damaged header or date leaves that value empty; processing continues.
- **Attachments**: every other part, and attached messages (`message/rfc822`) as nested `.eml` files. Names are sanitised like upload file names; the type is checked by content like an upload (the extension must match). Each one is extracted with the origin "załącznik: nazwa › …". Nesting deeper than `INGEST_MAX_DEPTH` (3), unsupported types (images, archives) and kinds not processed yet are skipped with a note. Text parts without a file name are alternative bodies, not attachments.

### Language

Each chunk gets `pl`, `en` or `unknown` from lingua, restricted to Polish and English and judged on its first 20,000 characters. A chunk with fewer than `LANGUAGE_MIN_LETTERS` (default 40) letters is `unknown` instead of a guess. The result is informational and a filter; it never selects the search index.

### Outbox and recovery

The event `document.version.extracted` is written to `ing_outbox` in the same transaction as the parts and delivered right after the commit (at least once; handlers are idempotent). Undelivered entries are delivered again with `SELECT … FOR UPDATE SKIP LOCKED`, so two deliverers never take the same entry.

At every worker start (one process per start does steps 2–4, guarded by a Redis lock for 60 s):
1. Temporary directories in `/data/storage/.work` older than twice the time limit (at least an hour) are removed.
2. Versions in `processing` for longer than `INGEST_STALE_MINUTES` (30) go back to the queue, or to `failed` ("Przetwarzanie zostało przerwane zbyt wiele razy…") when they used all attempts.
3. Versions in `pending` are enqueued again (a version that is already queued is skipped by the second job).
4. Undelivered events are delivered; delivered ones older than `OUTBOX_RETENTION_DAYS` (7) are removed.

The same work on demand: `baza-wiedzy reprocess --stuck` (steps 2–3) and `baza-wiedzy events redeliver` (step 4).

### Virus scan hook

Before extraction the pipeline calls `ingestion/scanning.py` with the path of the original. With `SCAN_HOOK=none` (the default and, for now, the only value) it does nothing. A ClamAV scanner would be added there (the `clamav` service of the `antivirus` Compose profile, `INSTREAM`): an infected file raises a permanent error (the version fails, nothing is extracted), an unreachable scanner a transient one (retried). Whether company policy requires it is open question 6 in [decisions.md](decisions.md).

## OCR

Decision per PDF:
- Extract text per page with pypdfium2.
- A page "needs OCR" when it has fewer than `OCR_MIN_CHARS` non-whitespace characters (default 25) and contains an image.
- If any page needs OCR, run OCRmyPDF with `--skip-text` (pages that already have text are untouched), languages `pol+eng`, `--rotate-pages`, `--deskew`, a job limit and a timeout (`OCR_TIMEOUT_SECONDS`, default 1800).
- Re-extract text from the OCR'd pages, set `ocr_applied = true` on those parts and on the version.
- Store the resulting PDF as the derived file (searchable copy). The original stays untouched and is what "download" returns.

Known limits to mention to users: OCR quality depends on scan quality; handwriting is not supported; very large scans can take minutes; password-protected PDFs fail with the message "PDF is password protected".

## Safety limits

| Limit | Default | Setting |
|---|---|---|
| Max upload size | 50 MB (proxy: `MAX_REQUEST_MB` 51) | `MAX_UPLOAD_MB` |
| Max e-mail attachment depth | 3 | `INGEST_MAX_DEPTH` |
| Max parts (chunks) per version | 2000 | `INGEST_MAX_PARTS` |
| Max extracted text per version | 50 MB (characters) | `INGEST_MAX_TEXT_MB` |
| ZIP (DOCX): entries, total unpacked size, ratio of entries above 1 MB | 5000, 200 MB, 100× (checked before unpacking) | fixed in `extractors/docx.py` |
| XML (DOCX) | no DTDs, no entities, no network | fixed |
| Time limit of one attempt | 900 s | `INGEST_TIME_LIMIT_SECONDS` |
| Attempts (transient errors only) | 3, delay 30 s doubling up to 600 s | `INGEST_MAX_ATTEMPTS`, `INGEST_RETRY_MIN_SECONDS`, `INGEST_RETRY_MAX_SECONDS` |
| LibreOffice timeout | 120 s | Phase 2 stage C |
| OCR timeout | 1800 s | Phase 4 |
| Worker processes (one job each) | 2 | `WORKER_PROCESSES` |

Each attempt runs in its own temporary directory under `/data/storage/.work`, removed afterwards. The worker container has a memory limit (`WORKER_MEMORY_LIMIT`).

## Operations

- **Process again**: the button "Przetwórz ponownie" under the extracted text (owner with `documents.upload` for own documents, `documents.edit_any` for any document), or `baza-wiedzy reprocess <document or version ID> …` / `baza-wiedzy reprocess --failed` in the `app` container. Both reset the attempt counter and are audited (`document.reprocess`). While an attempt is running the version cannot be reprocessed ("Dokument jest właśnie przetwarzany…").
- **Stuck in "przetwarzanie"** (worker killed): `baza-wiedzy reprocess --stuck`, or restart the worker. Expected output: `Stuck versions: 1 queued again, 0 failed (no attempts left); waiting versions queued again: 0.`
- **Event outbox**: `baza-wiedzy events redeliver` → `Events delivered: N, failed: 0, still undelivered: 0; old delivered entries removed: M.` Exit code 1 when a handler failed (details in the worker or CLI log: event and exception class).
- After changing extractors or the search configuration, `baza-wiedzy reindex-search` (see [search.md](search.md), Phase 3) or reprocessing may be needed.
- Failure reasons are visible to everyone who can see the document; details are in the worker log (`make logs`, search for the version ID).

## Testing

All test files are synthetic and generated in the tests (`tests/fixtures/samples.py`, `tests/fixtures/ole2.py`); nothing binary is committed.

- Unit (`tests/unit/test_ingestion_extractors.py`): TXT in UTF-8, UTF-8 with BOM, UTF-16, Windows-1250 and ISO-8859-2 (also two-letter texts), refused CP852 and binary data; text limit; chunking of 800,000 characters and of text without spaces; language (pl, en, short → unknown, configurable threshold); HTML to text (scripts, styles, tracking image, links); EML with HTML alternative, DOCX/TXT/PNG attachments, sanitised attachment names, damaged headers and dates, nested messages beyond depth 3, damaged attachment; DOCX with table and header, zip bomb, external entity (XXE), damaged file.
- Integration (`tests/integration/test_ingestion.py`, `make test-db`, real worker on Redis): TXT and EML with a DOCX attachment from upload to "gotowe" with parts, language, e-mail headers, notes and the delivered event; long TXT in three chunks; processing twice without duplicates; permanent error without retry; transient errors with backoff and giving up; unexpected errors without the exception message in the log; logs without text, names or headers; stuck recovery; superseded attempts; outbox with `SKIP LOCKED`, failing handlers and pruning; worker start (temporary files, pending versions, one recovery per start); "Przetwórz ponownie" permissions (404/403/allowed, busy) and the CLI.
- `make smoke` uploads a small TXT file through the proxy and waits for "gotowe".
- Still to come (stage C): text PDF, PDF without text, encrypted PDF, DOC, MSG with an attachment.
