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
| `proxy` | `baza-wiedzy-proxy` (Caddy, built locally from `docker/caddy`) | TLS termination, reverse proxy, request size limit; runs as non-root on 8080/8443 inside the container | 80, 443 |
| `app` | `baza-wiedzy-app` | Web UI, API, permissions, search queries | internal |
| `worker` | `baza-wiedzy-worker` | Extraction, OCR, indexing (Tesseract, LibreOffice inside) | none |
| `db` | `baza-wiedzy-db` | PostgreSQL 16 + Polish Hunspell dictionary | none |
| `redis` | `redis` 8.2 | Job queue only (no business data); append-only file on, so queued jobs survive a restart; password required | none |
| `clamav` | `clamav` | Optional virus scan, compose profile `antivirus`, off by default | none |

Redis licence: Redis 8 is offered under AGPLv3 (among others), which is acceptable for internal, unmodified use. If that is ever in doubt, [Valkey](https://valkey.io) (BSD-3-Clause) is a 1:1 replacement: change the `redis` image to `valkey/valkey` with a pinned tag; nothing else changes.

`app` and `worker` share the same Python code base and database models; they differ in image contents (the worker image carries OCR and LibreOffice) and start command.

## Modular structure

The application is a modular monolith: one codebase and one image per role (`app`, `worker`), made of an always-on **core** and optional **modules**. `ENABLED_MODULES` in `.env` selects the modules; `app` and `worker` must use the same value.

| Part | Requires | Owns tables | Publishes events | Subscribes to |
|---|---|---|---|---|
| core | none | `users`, `roles`, `sessions`, `audit_log`, `app_settings` | none | none |
| `documents` | none | `doc_*` | `document.version.uploaded`, `document.published` | none |
| `ingestion` | `documents` | none in the MVP (`ing_*` reserved) | `document.version.extracted` | `document.version.uploaded` |
| `search` | `documents` | `srch_*` | none | `document.version.extracted` |

Rules (enforced in CI by import-linter, see ADR-014):
- A module never imports the internals of another module. It may import `baza_wiedzy.core` and the `services/api.py` of a module it requires. Everything else goes through events.
- Events are synchronous and in-process: a handler runs in the process that publishes the event. The `worker` therefore also loads `search` and indexes extracted text inside the worker; in `app` the `ingestion` handler only enqueues the job.
- At startup the registry checks `requires` and stops with a readable message when a required module is not enabled.
- Permissions, menu items, routers, background tasks, CLI commands and migration branches are collected from the module manifests (`module.py`).
- There are no plugins from outside the repository.

Per-module documentation: [documents](modules/documents.md), [ingestion](modules/ingestion.md), [search](modules/search.md).

## Technology stack

See `CLAUDE.md` section 3 for the authoritative table. Short version: Python 3.12, FastAPI, Jinja2 + HTMX, SQLAlchemy 2 (sync) + psycopg 3, Alembic, PostgreSQL 16 full-text search with `pg_trgm` and `unaccent`, Dramatiq + Redis, pypdfium2, OCRmyPDF + Tesseract, ldap3, Caddy.

## Main flows

### Upload
1. User with `documents.upload` sends a file.
2. `app` validates it (size, type by content), stores it under a UUID key, writes `doc_documents` and `doc_versions` rows with `processing_status = pending`, publishes the event `document.version.uploaded` (the `ingestion` module enqueues `process_version(version_id)`), writes an audit entry.
3. The browser polls the status of the version (HTMX) and shows progress.
4. `worker` extracts text, runs OCR if needed, detects language, publishes `document.version.extracted` with the text parts (the `search` module stores and indexes them in `srch_parts`) and sets status `done` or `failed` through `documents.services.api`. Details: [ingestion-and-ocr.md](ingestion-and-ocr.md).

### Publish
A user with `documents.publish` (own documents) or `documents.publish_any` publishes a version: `doc_documents.published_version_id` is set and status becomes `published`. Only then do readers see and find it.

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
- Modules talk to each other only through `services/api.py` and events.
- Config through environment variables only.
- UI text: message ids are English and wrapped in `_()` in templates and code; the Polish catalog is `src/baza_wiedzy/core/web/locale/pl/LC_MESSAGES/messages.po`. It is compiled in memory at startup (no `.mo` files in the repository or the image). After changing UI text run `make messages` and translate the new `msgstr` entries; a unit test fails while any string is untranslated. `APP_NAME` comes from configuration and is never hard-coded.
- Static files (CSS, HTMX) are served by `app` from `src/baza_wiedzy/core/web/static/`; nothing is loaded from a CDN. HTMX 2.0.11 is vendored in `static/vendor/` with its licence (0BSD). Templates link to paths only, never to absolute URLs.
- Background jobs: Dramatiq on Redis. Actors are declared in a module and listed in its manifest (`tasks`); at start `baza_wiedzy.worker` binds the actors of the enabled modules to the broker. Each worker process writes a heartbeat key in Redis every 15 s (expires after 60 s); `/ready` checks it. Test job: `core.ping` (`baza-wiedzy worker-ping`).
- Logs: JSON to stdout, collected by Docker with rotation.
- Health: `/health` (process is alive), `/ready` (database, Redis, worker heartbeat, schema version).
