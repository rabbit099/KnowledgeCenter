# Ingestion and OCR

> Applies to: v0.2.1 · Last reviewed: 2026-11-17

How an uploaded file becomes searchable text. Upload validation and storage belong to the `documents` module; the processing described here belongs to the `ingestion` module (see [modules/ingestion.md](modules/ingestion.md)).

## Supported formats

| Format | Extractor | Notes |
|---|---|---|
| PDF with text | pypdfium2, per page | One `srch_parts` row per page |
| PDF scanned | OCRmyPDF + Tesseract (`pol+eng`) | Only pages without text are OCR'd |
| DOCX | python-docx (paragraphs, tables, headers, footers) + raw XML fallback | Text boxes and footnotes read from the XML |
| DOC | LibreOffice headless conversion to text | Isolated profile, timeout |
| TXT | charset-normalizer, then UTF-8 | Handles UTF-8, Windows-1250, ISO-8859-2 |
| EML | stdlib `email` | Headers, text body (HTML body converted to text), attachments |
| MSG | `extract-msg` | Same output as EML |

Attachments of EML/MSG that are PDF, DOCX, DOC, TXT, EML or MSG are extracted recursively (depth ≤ 3) and stored as `srch_parts` of type `attachment`. Other attachments (images, archives) are listed by name only; their file names remain searchable. OCR of standalone images is out of scope for the MVP.

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
5. A draft document with version 1 is created (`doc_documents`, `doc_versions`, `processing_status = pending`); title = the optional "Tytuł" field or the file name without extension.
6. Duplicates: the document page shows "Możliwy duplikat" with links when another document **visible to the uploader** has a version with the same SHA-256. Other people's drafts are never compared, so the warning reveals nothing. The upload is not blocked; removing a duplicate comes with the lifecycle (Phase 5).
7. Audit `document.upload` with the document id, `version_id`, `sha256`, `size_bytes` and `kind` (no file name, no content). After the commit the event `document.version.uploaded` is published (`ingestion` enqueues processing, stage B).
8. Virus scan: a scan step in the processing pipeline, disabled by default (stage B); ClamAV is not wired in Phase 2.

Drafts are visible only to the uploader and to holders of `documents.read_drafts` (Admin); anyone else gets **404** for the document page, so its existence is not revealed.

## Processing (in `worker`)

```
pending → processing → done
                    ↘ failed (with processing_error)
```

`done` may carry `processing_warnings` (for example one attachment could not be read).

Steps:
1. Detect the kind from content, not from the extension.
2. Run the extractor. Each result is a list of parts: text, page number, name, parent.
3. Chunk text longer than 100,000 characters.
4. Detect language per part (Polish/English via lingua; short text → `unknown`).
5. Publish the event `document.version.extracted` with the parts and metadata; the `search` module stores them in `srch_parts` (the database computes `tsv_pl`, `tsv_en`, `tsv_simple`).
6. For EML/MSG fill `doc_email_metadata` through `documents.services.api`.
7. Set `processing_status = done` through `documents.services.api`, write an audit-independent log line with duration and counts.

Jobs are idempotent: reprocessing publishes the event again and `search` replaces the existing parts of the version. Retries: up to 3 with backoff for transient errors (database, Redis, timeouts); permanent errors fail immediately with a readable message.

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
| Max e-mail attachment depth | 3 | `MAX_ATTACHMENT_DEPTH` |
| Max total extracted text per version | 50 MB | `MAX_EXTRACTED_TEXT_MB` |
| ZIP expansion ratio (DOCX) | 100× | `MAX_ZIP_RATIO` |
| LibreOffice timeout | 120 s | `LIBREOFFICE_TIMEOUT_SECONDS` |
| OCR timeout | 1800 s | `OCR_TIMEOUT_SECONDS` |
| Worker concurrency | 2 | `WORKER_CONCURRENCY` |

Each job runs in its own temporary directory under `/data/tmp`, removed afterwards. The worker container has a memory limit.

## Operations

- A stuck or failed version can be reprocessed from the admin UI or with `baza-wiedzy reprocess <version_id>`.
- After changing extractors or the search configuration, `baza-wiedzy reindex-search` (see [search.md](search.md)) or `baza-wiedzy reprocess --all` may be needed.
- Failure reasons are visible to the uploader and to admins.

## Testing

Fixtures (synthetic) for: text PDF, scanned PDF (image only), mixed PDF, encrypted PDF, DOCX with table and text box, DOC, TXT in UTF-8 and Windows-1250, EML with PDF attachment, EML with HTML-only body, MSG with attachment, nested EML in EML, zip-bomb-like DOCX (must be rejected).
