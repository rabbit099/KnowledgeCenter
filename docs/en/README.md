# Baza Wiedzy — Documentation (English)

> Applies to: v0.3.0 · Last reviewed: 2026-11-17

Baza Wiedzy ("Knowledge Base", working name) is an internal web application. People upload documents (PDF, DOCX, DOC, TXT, EML, MSG); the system reads their text (OCR for scans) and finds documents containing a typed phrase. It runs in Docker on a Linux server.

Polish version of this documentation: [`../pl/`](../pl/README.md).

## Contents

| Document | What it covers |
|---|---|
| [architecture.md](architecture.md) | Services, data flow, technology stack |
| [data-model.md](data-model.md) | Database tables, relations, indexes |
| [search.md](search.md) | How search works: Polish/English indexing, ranking, snippets |
| [ingestion-and-ocr.md](ingestion-and-ocr.md) | Upload pipeline, text extraction per format, OCR |
| [auth-and-permissions.md](auth-and-permissions.md) | Active Directory login, roles, permission catalog, audit |
| [deployment-operations.md](deployment-operations.md) | Installation, configuration, updates, rollback, backup, monitoring |
| [modules/documents.md](modules/documents.md), [modules/ingestion.md](modules/ingestion.md), [modules/search.md](modules/search.md) | Per module: scope, dependencies, permissions, tables, events, extension points |
| [decisions.md](decisions.md) | Decision log (ADR), assumptions and open questions |
| [changelog.md](changelog.md) | What changed in each version |

## Roadmap

Tick items when they are finished and documented.

### Phase 0 — Foundation
- [x] Repository, `pyproject.toml`, Makefile, pre-commit
- [x] Module registry: manifests, `ENABLED_MODULES`, event bus, boundary checks (import-linter)
- [x] Docker Compose: proxy, app, worker, db, redis (+ optional ClamAV profile)
- [x] Custom Postgres image with Polish Hunspell dictionary; spike result documented in `search.md`
- [x] Alembic baseline, `/health` and `/ready`
- [x] CI (lint, types, tests, docs-check, image build, smoke test); release workflow used for v0.1.0
- [x] `scripts/check_docs.py`

### Phase 1 — Users and permissions
- [x] Local break-glass admin (CLI), sessions, CSRF, login rate limit
- [x] Active Directory login, group → role mapping
- [x] Roles and permission catalog, admin UI for users and roles
- [x] Audit log and its viewer

### Phase 2 — Upload and ingestion
- [x] Upload with validation, storage, SHA-256, duplicate warning
- [x] Extractors: PDF (text), DOCX, DOC, TXT, EML, MSG (+ attachments)
- [x] Worker pipeline, statuses, retries, reprocess action
- [x] Language detection per part

### Phase 3 — Search
- [ ] Indexing in `pl`, `english`, `simple_unaccent`
- [ ] Search UI (HTMX), snippets, filters, pagination
- [ ] Metadata search with `pg_trgm`
- [ ] Golden search tests and performance test (20,000 documents)
- [ ] Index the text parts of versions in "wymaga OCR" (`needs_ocr`) too: their text pages are already extracted
- [ ] `search` reads parts through `ingestion.services.api` and declares `requires=("documents", "ingestion")` (ADR-020)

### Phase 4 — OCR
- [x] Detection of pages without text (Phase 2: pages marked, version "wymaga OCR")
- [ ] OCRmyPDF (`pol+eng`) for the marked pages of `needs_ocr` versions, then `done`
- [ ] Derived searchable PDF, `ocr_applied` flag, timeouts and limits

### Phase 5 — Document lifecycle
- [ ] Draft / published / archived, versions, publishing a chosen version
- [ ] Categories
- [ ] Preview in the browser (stretch goal)

### Phase 6 — Operations
- [ ] Release workflow (tags → GHCR)
- [ ] `update.sh`, `rollback.sh`
- [ ] `backup.sh`, `restore.sh`, restore test procedure
- [ ] Operator guide complete

### Phase 7 — Hardening and acceptance
- [ ] Security review against `CLAUDE.md` section 8
- [ ] Acceptance test with real documents (including scans and e-mails)
- [ ] User guide for readers and publishers
- [ ] On the server: the audit log and login limits see the real client addresses, not the Docker gateway (check in [auth-and-permissions.md](auth-and-permissions.md#authentication), client address)
- [ ] Separate database roles (schema owner for migrations vs application role without ownership rights) and `REVOKE UPDATE, DELETE, TRUNCATE` on `audit_log`
- [ ] Remove the `exclude-newer-package` exception for `opentelemetry-api` at the monthly dependency review once `exclude-newer` is past 2026-10-30 (ADR-015)
- [ ] On the server: the worker has no internet access (`make smoke` network check, or the command in deployment-operations.md, Security notes)
