# Ingestion and OCR

> Applies to: v0.3.0 · Last reviewed: 2026-11-17

How an uploaded file becomes searchable text. Upload validation and storage belong to the `documents` module; the processing described here belongs to the `ingestion` module (see [modules/ingestion.md](modules/ingestion.md)).

## Supported formats

| Format | Extractor | Status |
|---|---|---|
| TXT | own encoding rule: UTF-8, Windows-1250, ISO-8859-2 ([Text encodings](#text-encodings), ADR-022) | Phase 2 |
| EML | stdlib `email` (policy=default): headers, text body (HTML body converted to text), attachments | Phase 2 |
| DOCX | python-docx (paragraphs and tables in order, headers, footers) + hardened raw-XML fallback | Phase 2 |
| PDF with text | pypdfium2 in a child process, one part per page | Phase 2 |
| DOC | LibreOffice headless converts to DOCX in a child process, then the DOCX extractor | Phase 2 |
| MSG | `extract-msg`: headers, plain / HTML / RTF body, attachments, attached messages | Phase 2 |
| PDF scanned | pages without a text layer are marked "wymaga OCR" now; OCRmyPDF + Tesseract (`pol+eng`) | Phase 4 |

Attachments of EML/MSG that are themselves supported files are extracted recursively (depth ≤ `INGEST_MAX_DEPTH`, default 3) and stored as parts of type `attachment` (or `page` for PDF pages) in `ing_parts`; attached Outlook messages count as one level each. Other attachments (images, archives) and password-protected ones are skipped with a note on the document page. OCR of standalone images is out of scope for the MVP.

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

The document page polls the status every 3 s while it is "oczekuje" or "przetwarzanie" (and updates the title, which processing may change) and then shows the e-mail headers, notes and the extracted parts (origin, language, size, a preview of the first 5,000 characters; a scanned page without text is shown as "brak warstwy tekstowej, wymaga OCR").

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
- **Headers** of the top-level message (subject, from, to, cc, date, Message-ID) go to `doc_email_metadata`. Addresses are stored in one format for EML and MSG: `Name <address>` (only the address when the message gives no name), several separated by ", "; BCC is not kept. Documents processed before 2026-11-17 keep the old format until "Przetwórz ponownie". The headers are written through `documents.services.api` and shown on the document page; they are never logged. A damaged header or date leaves that value empty; processing continues.
- **Attachments**: every other part, and attached messages (`message/rfc822`) as nested `.eml` files. Names are sanitised like upload file names; the type is checked by content like an upload (the extension must match). Each one is extracted with the origin "załącznik: nazwa"; nested ones are shown as "załącznik: a.msg › b.docx › strona 2" (the word once, before the first attachment). Nesting deeper than `INGEST_MAX_DEPTH` (3), unsupported types (images, archives) and kinds not processed yet are skipped with a note. Text parts without a file name are alternative bodies, not attachments.

### PDF, DOC and MSG

- **PDF**: one part per page with the origin "strona N" (inside an e-mail "załącznik: raport.pdf › strona 2"); the page count is stored on the version. A page with text becomes a part; a page with fewer than `OCR_MIN_CHARS` (25) non-space characters that contains an image is a scanned page: kept as a part marked `needs_ocr` (with its little text); a blank page (no text, no image) is skipped.
- **Status of scans** (proposal for Phase 4, no migration needed): a version with at least one marked page ends as `needs_ocr` ("wymaga OCR") instead of `done`; its text pages are already stored and will be searchable. Phase 4 takes the `needs_ocr` versions, runs OCR only on the marked pages (`--skip-text`), replaces those parts, sets `ocr_applied` and moves the version to `done`. "Przetwórz ponownie" works for them like for any other version.
- **DOC**: LibreOffice converts to DOCX, then the DOCX extractor reads it (tables "A | B", headers, footers).
- **MSG**: like EML. Body: plain text, else HTML converted to text, else RTF (HTML encapsulated in RTF through extract-msg, plain RTF with `rtf_text.py`). Attachments stored by value go through the same registry (type by content, origin "załącznik: nazwa"); attached Outlook messages are processed as nested messages (one depth level each); other kinds (OLE objects, links) are skipped with a note. The libraries' own loggers are silenced so that nothing from a message reaches the log. Without a typed title the subject becomes the title; a document page opened during processing shows the new title as soon as processing ends (the status polling also replaces the heading).

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

Implemented in Phase 2: the extraction per page and the marking of pages that need OCR (see [PDF, DOC and MSG](#pdf-doc-and-msg)); the OCR run itself comes in Phase 4.

Known limits to mention to users: OCR quality depends on scan quality; handwriting is not supported; very large scans can take minutes; password-protected PDFs fail with the message "PDF is password protected".

## Safety limits

| Limit | Default | Setting |
|---|---|---|
| Max upload size | 50 MB (proxy: `MAX_REQUEST_MB` 51) | `MAX_UPLOAD_MB` |
| Max e-mail attachment depth (EML, MSG, attached messages) | 3 | `INGEST_MAX_DEPTH` |
| Max parts (chunks) per version | 2000 | `INGEST_MAX_PARTS` |
| Max extracted text per version | 50 MB (characters) | `INGEST_MAX_TEXT_MB` |
| Max PDF pages | 2000 | `INGEST_MAX_PAGES` |
| ZIP (DOCX): entries, total unpacked size, ratio of entries above 1 MB | 5000, 200 MB, 100× (checked from the central directory, before anything is unpacked; LibreOffice's own DOCX output of a DOC: entries and size only) | fixed in `extractors/docx.py` |
| XML (DOCX) | no DTD loading, no entity expansion (internal "billion laughs" and external XXE), no network, depth limit of libxml2 (`huge_tree` off) | fixed |
| PDF child process: memory (address space), time | 1024 MB, 300 s | `INGEST_CHILD_MEMORY_MB`, `INGEST_CHILD_TIMEOUT_SECONDS` |
| LibreOffice: memory (address space), time, parallel conversions | 1024 MB, 120 s, one per worker container | `LIBREOFFICE_MEMORY_MB`, `LIBREOFFICE_TIMEOUT_SECONDS` |
| Time limit of one attempt | 900 s | `INGEST_TIME_LIMIT_SECONDS` |
| Attempts (transient errors only) | 3, delay 30 s doubling up to 600 s | `INGEST_MAX_ATTEMPTS`, `INGEST_RETRY_MIN_SECONDS`, `INGEST_RETRY_MAX_SECONDS` |
| OCR timeout | 1800 s | Phase 4 |
| Worker processes (one job each), worker memory | 2, 2 GB | `WORKER_PROCESSES`, `WORKER_MEMORY_LIMIT` |

Password-protected files fail with "…zabezpieczony hasłem. Wgraj kopię bez hasła.": a PDF when pdfium asks for a password, a DOC by its encryption flag (before LibreOffice starts), a DOCX already at upload (an encrypted Office file is an OLE2 container with `EncryptionInfo`). As attachments they are skipped with a note.

Each attempt runs in its own temporary directory under `/data/storage/.work`, removed afterwards.

### Native code in child processes

pdfium (PDF) and LibreOffice (DOC) are native code that may crash, hang or allocate without bound on a malicious or broken file. They run in a child process (`ingestion/sandbox.py`), never inside the worker process:
- own session and process group; on timeout the whole group gets SIGKILL (LibreOffice's helper processes included), and the group is killed after a normal end as well;
- address-space limit (`RLIMIT_AS`) and no core dumps; tested: LibreOffice 25.2 converts normally with 1 GB, pdfium's child fails cleanly (`MemoryError` → "Przetworzenie pliku wymaga więcej pamięci…");
- a minimal environment (no proxy or database variables), `HOME` and `TMPDIR` inside the attempt's directory, a fresh LibreOffice profile (`-env:UserInstallation`) per conversion, output only to files in that directory (stdout and stderr go to files, never to the log);
- one LibreOffice conversion at a time per worker container (a file lock in `/data/storage/.work`); a second worker process waits up to `LIBREOFFICE_TIMEOUT_SECONDS`, then retries later;
- no network: the worker joins only the internal Docker network `backend` (ADR-024, checked by `make smoke`);
- a crash (signal) is a permanent "damaged file" error, a timeout a transient one: retried, and after the last attempt "Przetwarzanie trwało dłużej niż limit czasu".

The worker container's memory limit applies on top. Measured on 2026-11-17 with the production Compose file and the defaults (2 worker processes): idle 173 MiB; while processing 26 files at once (among them a 300-page PDF, a 1.5 MB DOC with 3000 paragraphs, a 10 MB TXT and an e-mail with ten PDF attachments) at most 457 MiB (cgroup peak including page cache 495 MiB), all done in 18 s; 339 MiB afterwards (Python keeps freed memory). `WORKER_MEMORY_LIMIT=2g` therefore leaves room for one LibreOffice conversion (≤ 1 GB) and very large texts; raise it by about 0.6 GB per extra worker process.

## Operations

- **Process again**: the button "Przetwórz ponownie" under the extracted text (owner with `documents.upload` for own documents, `documents.edit_any` for any document), or `baza-wiedzy reprocess <document or version ID> …` / `baza-wiedzy reprocess --failed` in the `app` container. Both reset the attempt counter and are audited (`document.reprocess`). While an attempt is running the version cannot be reprocessed ("Dokument jest właśnie przetwarzany…").
- **Stuck in "przetwarzanie"** (worker killed): `baza-wiedzy reprocess --stuck`, or restart the worker. Expected output: `Stuck versions: 1 queued again, 0 failed (no attempts left); waiting versions queued again: 0.`
- **Event outbox**: `baza-wiedzy events redeliver` → `Events delivered: N, failed: 0, still undelivered: 0; old delivered entries removed: M.` Exit code 1 when a handler failed (details in the worker or CLI log: event and exception class).
- After changing extractors or the search configuration, `baza-wiedzy reindex-search` (see [search.md](search.md), Phase 3) or reprocessing may be needed.
- Failure reasons are visible to everyone who can see the document; details are in the worker log (`make logs`, search for the version ID).

## Testing

All test files are synthetic and generated in the tests (`tests/fixtures/samples.py`, `tests/fixtures/msg.py` on top of `tests/fixtures/ole2.py`); nothing binary is committed. DOC files are made by LibreOffice from HTML in the test image.

- Test image: target `test` of `docker/worker/Dockerfile` (the release stage plus uv; never published: release and smoke builds take the default, last stage `runtime`). `make test-db` runs the markers `integration` and `tools` (needs LibreOffice) there; `make test` on the host skips both. Building it reuses the worker's system layers: about 1 minute extra for the first build in CI, seconds with a cache; the worker image itself builds in 138 s without any cache.
- Unit: `tests/unit/test_ingestion_extractors.py` (TXT, chunks, language, HTML, EML, DOCX) and `tests/unit/test_ingestion_formats.py`: BOM for UTF-8 and UTF-16 LE/BE, long Windows-1250 vs ISO-8859-2 text; PDF per page, scanned and mixed pages, blank pages, password-protected, damaged and truncated PDFs, page limit; isolation (timeout kills the process group, memory limit, minimal environment); DOC through LibreOffice (private profile, no leftover processes), encrypted DOC refused before LibreOffice, damaged DOC, LibreOffice timeout, a large repetitive DOC; MSG headers, plain/HTML/RTF bodies, DOCX/PDF/PNG attachments, attached messages with their own attachments, depth limit, MSG inside EML, damaged MSG; RTF to text; billion laughs, deep XML nesting, too many ZIP entries; encrypted DOCX at upload and as attachments.
- Integration (`tests/integration/test_ingestion.py`, real worker on Redis): TXT and EML with DOCX; EML with PDF and MSG attachments; MSG with subject as title and e-mail metadata; DOC; scanned PDF → "wymaga OCR"; encrypted PDF → readable error without retry; child timeouts retried and reported; logs of PDF, MSG and damaged files without content; plus everything from stage B.
- `make smoke` uploads a small TXT and a small text PDF through the proxy and waits for "gotowe".
- `make probki DIR=<directory>` writes one sample of each case for a manual test, with the expected results in `OCZEKIWANE.txt`.
