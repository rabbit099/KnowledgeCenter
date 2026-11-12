# Ingestion and OCR

> Applies to: v0.1.0 · Last reviewed: 2026-11-12

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

1. Size ≤ `MAX_UPLOAD_MB` (default 100); the proxy enforces the same limit.
2. Extension in the allow-list **and** content type detected by libmagic matches the extension.
3. SHA-256 computed while streaming to disk.
4. Stored under a UUID key; the user's file name is kept only as metadata and never used as a path.
5. If the same SHA-256 already exists, the user gets a warning (not a block).
6. Optional virus scan with ClamAV when the `antivirus` profile is enabled.
7. Database rows created, the event `document.version.uploaded` published (`ingestion` enqueues the job), audit entry written.

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
| Max upload size | 100 MB | `MAX_UPLOAD_MB` |
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
