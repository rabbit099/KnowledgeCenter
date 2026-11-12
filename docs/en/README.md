# Baza Wiedzy — Documentation (English)

> Applies to: v0.0.0 (pre-release) · Last reviewed: 2026-11-12

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
- [ ] Docker Compose: proxy, app, worker, db, redis (+ optional ClamAV profile)
- [x] Custom Postgres image with Polish Hunspell dictionary; spike result documented in `search.md`
- [ ] Alembic baseline, `/health` and `/ready`
- [ ] CI (lint, types, tests, docs-check, image build)
- [x] `scripts/check_docs.py`

### Phase 1 — Users and permissions
- [ ] Local break-glass admin (CLI), sessions, CSRF, login rate limit
- [ ] Active Directory login, group → role mapping
- [ ] Roles and permission catalog, admin UI for users and roles
- [ ] Audit log and its viewer

### Phase 2 — Upload and ingestion
- [ ] Upload with validation, storage, SHA-256, duplicate warning
- [ ] Extractors: PDF (text), DOCX, DOC, TXT, EML, MSG (+ attachments)
- [ ] Worker pipeline, statuses, retries, reprocess action
- [ ] Language detection per part

### Phase 3 — Search
- [ ] Indexing in `pl`, `english`, `simple_unaccent`
- [ ] Search UI (HTMX), snippets, filters, pagination
- [ ] Metadata search with `pg_trgm`
- [ ] Golden search tests and performance test (20,000 documents)

### Phase 4 — OCR
- [ ] Detection of pages without text, OCRmyPDF (`pol+eng`)
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
