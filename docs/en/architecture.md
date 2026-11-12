# Architecture

> Applies to: v0.0.0 (pre-release) · Last reviewed: 2026-11-12

## Overview

A server-rendered web application. The browser talks only to the reverse proxy. Heavy work (text extraction, OCR) runs in a separate worker so uploads return immediately.

```
Browser ──HTTPS──► Caddy ──► app (FastAPI + Jinja2 + HTMX)
                              │        │
                              │        └──► Redis (job queue) ──► worker (Dramatiq)
                              │                                      │  pypdfium2, OCRmyPDF/Tesseract,
                              ▼                                      │  LibreOffice, extract-msg
                          PostgreSQL ◄───────────────────────────────┘
                     (data + full-text index)

                 Shared volume: /data/storage (original and derived files)
                 App ──LDAPS──► Active Directory
```

## Services (Docker Compose)

| Service | Image | Purpose | Published port |
|---|---|---|---|
| `proxy` | Caddy | TLS termination, reverse proxy, request size limit | 80, 443 |
| `app` | `baza-wiedzy-app` | Web UI, API, permissions, search queries | internal |
| `worker` | `baza-wiedzy-worker` | Extraction, OCR, indexing (Tesseract, LibreOffice inside) | none |
| `db` | `baza-wiedzy-db` | PostgreSQL 16 + Polish Hunspell dictionary | none |
| `redis` | `redis` | Job queue only (no persistent business data) | none |
| `clamav` | `clamav` | Optional virus scan, compose profile `antivirus`, off by default | none |

`app` and `worker` share the same Python code base and database models; they differ in image contents (the worker image carries OCR and LibreOffice) and start command.

## Technology stack

See `CLAUDE.md` section 3 for the authoritative table. Short version: Python 3.12, FastAPI, Jinja2 + HTMX, SQLAlchemy 2 (sync) + psycopg 3, Alembic, PostgreSQL 16 full-text search with `pg_trgm` and `unaccent`, Dramatiq + Redis, pypdfium2, OCRmyPDF + Tesseract, ldap3, Caddy.

## Main flows

### Upload
1. User with `documents.upload` sends a file.
2. `app` validates it (size, type by content), stores it under a UUID key, writes `documents` and `document_versions` rows with `processing_status = pending`, enqueues `process_version(version_id)`, writes an audit entry.
3. The browser polls the status of the version (HTMX) and shows progress.
4. `worker` extracts text, runs OCR if needed, detects language, writes `document_parts`, sets status `done` or `failed`. Details: [ingestion-and-ocr.md](ingestion-and-ocr.md).

### Publish
A user with `documents.publish` (own documents) or `documents.publish_any` publishes a version: `documents.published_version_id` is set and status becomes `published`. Only then do readers see and find it.

### Search
The user types a phrase; `app` builds the queries for each text-search configuration, applies the visibility rules inside SQL, ranks, then adds snippets for the visible page only. Details: [search.md](search.md).

### Login
Active Directory over LDAPS (service account finds the user, then a bind with the user's own credentials verifies the password), groups map to roles. A local break-glass admin exists for emergencies. Details: [auth-and-permissions.md](auth-and-permissions.md).

## Storage

```
/data/storage/<aa>/<bb>/<uuid>          original file (immutable)
/data/storage/derived/<uuid>.pdf        OCR'd (searchable) PDF, previews
/data/tmp/                              worker scratch space (cleaned after each job)
```

`<aa>/<bb>` are the first characters of the UUID, to avoid huge directories.

## Cross-cutting rules

- Visibility is enforced in SQL. Permission checks are in services and dependencies.
- Search code is isolated behind the `SearchBackend` interface.
- Config through environment variables only.
- Logs: JSON to stdout, collected by Docker with rotation.
- Health: `/health` (process is alive), `/ready` (database, Redis, worker heartbeat, schema version).
