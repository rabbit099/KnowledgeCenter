# Ingestion and OCR

> Applies to: v0.4.1 · Last reviewed: 2026-11-20

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
| PDF scanned | pages without a text layer are recognised by OCR (OCRmyPDF + Tesseract, `pol+eng`), also in e-mail attachments; text only | Phase 4, stage A |

Attachments of EML/MSG that are themselves supported files are extracted recursively (depth ≤ `INGEST_MAX_DEPTH`, default 3) and stored as parts of type `attachment` (or `page` for PDF pages) in `ing_parts`; attached Outlook messages count as one level each. Other attachments (images, archives) and password-protected ones are skipped with a note on the document page. OCR of standalone images is out of scope for the MVP.

## Upload validation (in `app`)

Page "Moje dokumenty" → "Wgraj dokumenty" (`/documents/upload`, permission `documents.upload`). One form takes several files (ADR-032); each file goes through the steps below **on its own** (own transaction, audit entry and event), so a refused or failing file never stops the others. The form is a plain multipart POST and works without JavaScript: the CSRF token is its **first** field, which the CSRF middleware reads from the beginning of the body (the files stream on untouched); HTMX sends it in the `X-CSRF-Token` header as well. A small script (`static/js/upload.js`) adds drag and drop and checks the limits before sending.

Limits of one form: at most `UPLOAD_MAX_FILES` files (default 50; further files are refused per file, more than 50 above the limit refuse the whole form) and `UPLOAD_MAX_TOTAL_MB` together (default 500, capped at `MAX_REQUEST_MB` − 1; a larger `Content-Length` refuses the form with "Pliki są razem większe niż dozwolony rozmiar (… MB). Wgraj je w kilku częściach."). The proxy cuts bodies above `MAX_REQUEST_MB` (default 501) and answers with a Polish page instead of an empty error. The files are spooled by Starlette to the app container's temporary directory: keep `UPLOAD_MAX_TOTAL_MB` of free disk there.

Results: one file uploaded → its document page, as before; one file refused → the form with the message, as before; otherwise the browser goes to "Moje dokumenty" with the table "Wynik wgrywania" (per file: uploaded, possible duplicate, skipped, refused with the reason) and a summary. The table is a small JSON file `/data/storage/.batches/<random id>.json` readable only by the uploader, kept `UPLOAD_BATCH_HOURS` (default 24) and removed at a later upload.

1. Size: the request is refused early when `Content-Length` exceeds `MAX_UPLOAD_MB` (default 50), and the copy stops as soon as the real size passes it (HTTP 413, "Plik jest większy niż dozwolony rozmiar."; with several files: that file is refused, the others continue).
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
6. Duplicates: when another document **visible to the uploader** has a version with the same SHA-256, the file is not stored yet but **held** (`/data/storage/.batches/<id>/<n>.part`, same volume) as "możliwy duplikat" with links to those documents; the uploader chooses "Wgraj mimo to" (the held file is stored and steps 4, 5, 7 run; the document page then shows "Możliwy duplikat" as before) or "Pomiń" (the held file is deleted). Each decision is a POST to `/documents/upload/batches/<id>/<n>` (CSRF, permission `documents.upload`, only the uploader's own batch; a second decision is a no-op). Other people's drafts are never compared, so the hold reveals nothing.
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
- **Status of scans** (Phase 4): the scanned pages are recognised by OCR in the same pass (section [OCR](#ocr)); the version ends as `done` with `ocr_applied`. It ends as `needs_ocr` ("wymaga OCR") only when OCR is off (`OCR_ENABLED=false`), the PDF has more scanned pages than `OCR_MAX_PAGES`, or OCR failed (time limit, crash); its text pages are stored and searchable either way, and "Przetwórz ponownie" tries again.
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

Phase 4, stage A (ADR-031). Per PDF, also a PDF attached to an e-mail (EML, MSG):
1. Text per page with pypdfium2 (child process). A page "needs OCR" when it has fewer than `OCR_MIN_CHARS` non-whitespace characters (default 25) and contains an image.
2. If any page needs OCR and OCR is on, OCRmyPDF runs **only on those pages** (`--mode force --pages 2,5,…`) in a child process: languages `OCR_LANGUAGES` (`pol+eng`), automatic rotation of sideways pages (`--rotate-pages`, confidence `OCR_ROTATE_THRESHOLD`, default 5), pages rasterised by pypdfium (`--rasterizer pypdfium`), **text only** (`--sidecar`, `--output-type none`): no derived PDF is stored, the original is what "Pobierz oryginał" returns.
3. The recognised text replaces those pages' parts; they get `ocr_applied` (and so does the version); the version ends as `done` ("gotowe"). A page from which OCR gets fewer than `OCR_MIN_CHARS` characters is noted: "OCR nie znalazł (prawie) żadnego tekstu na stronach …" (handwriting, a picture, a very poor scan).
4. OCR never fails a document: if it is off, the PDF has more scanned pages than `OCR_MAX_PAGES`, or it fails (time limit `OCR_TIMEOUT_SECONDS`, a crash), the scanned pages stay marked "wymaga OCR" with a note on the text panel, the text pages are searchable, and "Przetwórz ponownie" tries again.

The document page shows "Tekst: tekst z OCR" with a warning that words and especially numbers may contain recognition errors; the text panel marks each recognised page "tekst z OCR".

Choices measured on synthetic scans (stage A): no `--deskew` (its fixed-threshold binarisation turned a low-contrast scan into an empty page; Tesseract reads a skew of up to about 5° by itself, 8° fails either way); rotation threshold 5 instead of OCRmyPDF's default 14 (a page turned by 90° stayed unturned and came out as garbage); no Ghostscript (not needed with the pypdfium rasteriser and text-only output; not installed). Isolation, limits and the absence of OCR text in logs: see [Safety limits](#safety-limits) and [Native code in child processes](#native-code-in-child-processes).

Coming in Phase 4: stage B a separate queue and service for OCR, automatic OCR up to `OCR_AUTO_MAX_PAGES` (30) scanned pages and "Uruchom OCR" above, OCR statuses, `baza-wiedzy ocr --pending`; stage C the label in search results and measurements (time per page, `tessdata_best`).

Known limits to tell users: quality depends on the scan; handwriting is not supported; numbers (tax numbers, account and phone numbers) may be misread; large scans take minutes; password-protected PDFs fail with "PDF jest zabezpieczony hasłem".

## Safety limits

| Limit | Default | Setting |
|---|---|---|
| Max upload size per file; files and total size of one form | 50 MB; 50 files, 500 MB (proxy: `MAX_REQUEST_MB` 501) | `MAX_UPLOAD_MB`; `UPLOAD_MAX_FILES`, `UPLOAD_MAX_TOTAL_MB` |
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
| OCR (per PDF): scanned pages, time, time per page, Tesseract processes, memory (address space) per process, page image size | 200 pages, 600 s, 120 s, 2, 2048 MB, 200 megapixels (more scanned pages: kept "wymaga OCR" with a note) | `OCR_MAX_PAGES`, `OCR_TIMEOUT_SECONDS`, `OCR_PAGE_TIMEOUT_SECONDS`, `OCR_JOBS`, `OCR_MEMORY_MB`, `OCR_MAX_IMAGE_MPIXELS` |
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
