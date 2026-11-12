# CLAUDE.md — Baza Wiedzy (working name)

Guidance for Claude Code. Read this file at the start of every session. Keep it accurate: if reality and this file disagree, fix this file in the same change.

## 1. Project

Internal company knowledge base. Users upload documents (PDF, DOCX, DOC, TXT, EML, MSG); the system extracts and indexes their text (OCR for scans); users type a phrase and get the documents that contain it, with highlighted snippets.

- Runs in Docker (Compose) on a Linux server. One person (Jakub, IT specialist) builds it with Claude Code and operates it. Everything must be simple to run, update, back up and debug.
- Working name: "Baza Wiedzy". The name is configurable (`APP_NAME`); never hard-code it in code or templates.
- UI language: Polish, through gettext/Babel catalogs (other languages can be added later). Code, comments, commit messages and this file: English. Documentation: English **and** Polish (section 9).
- Scale: ~200 documents at start. Design and test for up to 20,000 documents and 100 users.

## 2. MVP scope

Must have:
- Upload of PDF (text and scanned), DOCX, DOC, TXT, EML, MSG. Email attachments are extracted and indexed too.
- OCR for scanned PDFs (Polish + English).
- Full-text search in Polish and English, phrase search, snippets with highlighting, filters.
- Document lifecycle: draft → published → archived, with versions.
- Admin module: users, roles (admin can create new roles and assign permissions), categories, audit log, settings.
- Login through Active Directory (LDAP over TLS) plus one local break-glass admin.
- Default roles: Admin, Publisher, Reader.
- Audit log (GDPR/RODO): who uploaded, published, downloaded, deleted, changed permissions, logged in.
- Update mechanism (`scripts/update.sh`), backup and restore scripts, health endpoints.

Out of scope unless explicitly requested: SSO/Kerberos, per-category access control lists, semantic/AI search, UI in other languages, editing documents in the browser, comments, self-update from the web UI, OCR of standalone image files.

## 3. Stack (decided — change only through an ADR in `docs/en/decisions.md`)

| Area | Choice |
|---|---|
| Language | Python 3.12, dependency management with `uv`, `pyproject.toml` |
| Web | FastAPI + Jinja2 + HTMX (server-rendered, no SPA, no Node build step if avoidable) |
| DB access | SQLAlchemy 2.x (sync) + psycopg 3, Alembic migrations |
| Database | PostgreSQL 16 (custom image with the Polish Hunspell dictionary), extensions `pg_trgm`, `unaccent` |
| Queue | Dramatiq + Redis (OCR and parsing run in the `worker` service) |
| PDF text | pypdfium2 (per page) |
| OCR | OCRmyPDF + Tesseract (`pol` + `eng`) |
| DOCX | python-docx with a raw-XML fallback |
| DOC | LibreOffice headless conversion (isolated profile, timeout) |
| EML / MSG | stdlib `email` (policy=default) / `extract-msg` |
| Encoding | charset-normalizer (TXT in UTF-8, Windows-1250, ISO-8859-2) |
| Language detection | lingua-language-detector restricted to Polish and English |
| Auth | Server-side sessions in a cookie (not JWT), argon2id for local passwords, `ldap3` for AD |
| Proxy / TLS | Caddy |
| CSS | Plain CSS or a small framework served locally (no CDN calls at runtime) |
| Quality | ruff, mypy, pytest, pytest-cov, pre-commit, import-linter (module boundaries) |
| CI/CD | GitHub Actions → images in GHCR (`app`, `worker`, `db`, `proxy`; ADR-017) |

## 4. Repository layout

```
baza-wiedzy/
├── CLAUDE.md
├── README.md                  # short, points to docs/
├── Makefile
├── pyproject.toml
├── docker-compose.yml         # production
├── docker-compose.dev.yml     # dev overrides (mounts, reload)
├── docker-compose.test.yml    # make test-db: throwaway DB (tmpfs) + one-off test runner, no host ports
├── .env.example               # every variable documented
├── alembic.ini
├── alembic/                   # migrations
├── docker/
│   ├── app/Dockerfile
│   ├── worker/Dockerfile      # + tesseract pol/eng, ocrmypdf, libreoffice
│   ├── db/                    # Dockerfile, dictionary/ (Hunspell pl + licence, polish.stop), initdb/ (app role only), spike.sql
│   └── caddy/                 # Dockerfile (non-root) + Caddyfile
├── src/baza_wiedzy/
│   ├── main.py  cli.py
│   ├── core/              # always enabled (section 6a)
│   │   ├── config.py  registry.py  events.py  module.py   # settings, module registry, event bus, core manifest
│   │   ├── db/            # models, session
│   │   ├── auth/          # providers (local, ldap), sessions, csrf
│   │   ├── permissions/   # permission checks (the catalog is assembled from manifests)
│   │   ├── audit/
│   │   ├── admin/         # admin panel skeleton
│   │   └── web/           # base templates, static/, locale/ (gettext)
│   └── modules/
│       ├── documents/     # module.py, routes, models, services/api.py, storage
│       ├── ingestion/     # module.py, extractors/, ocr.py, language.py, tasks.py, services/api.py
│       └── search/        # module.py, backend interface, postgres backend, query building, services/api.py
├── tests/             # unit/, integration/, search_golden/, fixtures/ (synthetic only)
├── scripts/           # check_docs.py, check_release.py, smoke_test.sh, offline_bundle.sh; later update.sh, rollback.sh, backup.sh, restore.sh
├── docs/
│   ├── en/
│   └── pl/
└── .github/workflows/ # ci.yml, release.yml
```

## 5. Commands (keep the Makefile as the single entry point)

`make up` · `make down` · `make logs` · `make test` · `make test-db` · `make smoke` · `make lint` · `make fmt` · `make migrate` · `make revision branch=<branch> m="message"` · `make shell` · `make seed-samples` · `make docs-check` · `make messages` · `make build`

If you add or rename a command, update this section and `docs/*/deployment-operations.md`.

## 6. Architecture rules

- Layers: routes (thin) → services (business logic) → db. Permission checks live in services/dependencies, never only in templates.
- Document visibility is enforced **in the SQL of every query** (search, lists, downloads), not by filtering results afterwards.
- Ingestion is asynchronous, idempotent and retryable. State lives in `document_versions.processing_status`. The upload request returns immediately.
- Module boundaries are rules, not suggestions: see section 6a.
- All search code lives in `src/baza_wiedzy/modules/search/` behind a `SearchBackend` interface, so the engine can be replaced (fallback: Meilisearch) without touching the rest.
- Configuration only through environment variables (pydantic-settings). Secrets through env or Docker secrets, never in the repo.
- Database stores UTC; the UI shows Europe/Warsaw.
- Migrations are forward-only and follow expand/contract so that version N−1 of the app still runs on the schema of version N. Never edit a migration that has been released.
- Original uploaded files are immutable. Derived files (OCR'd PDF, previews) are stored separately.

## 6a. Modular monolith

One codebase, one container image per role (`app`, `worker`), modules selected at start. Decisions: ADR-013, ADR-014 in `docs/en/decisions.md`.

- **Core** (always on): config, db, auth, permissions, audit, admin panel skeleton, module registry, event bus. **Modules**: `documents`, `ingestion`, `search` in `src/baza_wiedzy/modules/<name>/`. The core never imports a module.
- Enabled through `ENABLED_MODULES` (env). At startup the registry validates `requires` and fails with a readable error if a required module is missing. `ingestion` and `search` require `documents`.
- **Manifest** (`module.py`, `manifest = ModuleManifest(...)`): name, version, requires, permissions, menu items, routers, templates, migrations branch and path, settings schema, background tasks, CLI commands, event subscriptions (`on_register`). Code references in a manifest are lazy strings (`package.module:attr`), so reading manifests imports nothing heavy.
- **Boundaries**: a module never imports the internals of another module. Allowed: `baza_wiedzy.core.*` and another module's `services/api.py` (only if declared in `requires`). Everything else goes through in-process events defined in `core/events.py`. Enforced by import-linter (`make lint`, CI). Do not weaken the contracts to make a change compile; change the design.
- **Events** are synchronous, in-process and not persisted. Handlers must be idempotent. Current events: `document.version.uploaded`, `document.version.extracted`, `document.published`. New events and their payloads are added to `core/events.py` and documented in the module docs.
- **Data ownership**: every module owns its tables, prefixed `doc_` (documents), `ing_` (ingestion), `srch_` (search); core tables have no prefix. No module reads or writes another module's tables directly; it calls that module's `services/api.py`. A foreign key may point to a table of a module that this module `requires`. `search` owns the indexed text and indexes (`srch_parts`); `documents` owns document and version rows, processing status and e-mail metadata; `ingestion` updates status through `documents.services.api` and hands extracted text to `search` through the `document.version.extracted` event.
- **Visibility rule**: `documents.services.api` provides the SQL fragment that implements document visibility; `search` injects it into its queries (details in Phase 3). Visibility stays in SQL.
- **Migrations**: one Alembic branch per module (`branch_labels`) plus a `core` branch; a branch that `requires` another has `depends_on` it. `baza-wiedzy migrate` upgrades **all** branches, independent of `ENABLED_MODULES` (the schema is always complete); `/ready` compares the database with all script heads.
- **Permissions**: the catalog is assembled from manifests. Codes are fixed in code; admins combine them into roles but never invent codes.
- **Extension points** (inside the repository only, no dynamic plugin loading): extractor registry (new format = new file in `ingestion/extractors/`), `SearchBackend`, login providers (local, LDAP), file storage (in `documents`).
- A new module needs: manifest, `services/api.py`, import-linter contract, docs `docs/en/modules/<name>.md` and `docs/pl/modules/<name>.md`, tests.
- Keep it thin. No dependency injection framework, no plugin system, no per-module deployments.

## 7. Search rules (critical)

- Every text part is indexed in three text-search configurations at once: `pl` (Hunspell + simple fallback), `english`, `simple_unaccent`. The detected language is informational and a filter, never a switch that decides which index is used.
- Queries are built with `websearch_to_tsquery` for each configuration and combined with OR. Quoted phrases and `-exclusion` must work.
- Title, file name and e-mail headers are matched with tsvector **and** `pg_trgm`.
- Text is chunked (PDF per page, other text ≤ 100,000 characters per chunk) to stay under the 1 MB tsvector limit.
- `ts_headline` runs only on the visible page of results. Source text is not HTML-escaped by PostgreSQL: use sentinel markers for the highlight, escape in Python, then replace the sentinels with `<mark>`. Never mark raw snippets as safe.
- Changing a dictionary or configuration requires `baza-wiedzy reindex-search`. Document this whenever you touch search configuration.
- Keep the golden search tests in `tests/search_golden/` green (inflected forms, no-diacritics queries, English plurals, phrases, codes and numbers).

## 8. Security rules

- Validate uploads by content (libmagic), not only by extension. Enforce `MAX_UPLOAD_MB`. Store files under a generated UUID key, never under the user-supplied name. Beware path traversal in file names.
- Limit decompression and nesting: DOCX is a ZIP (zip-bomb limits), e-mail attachment depth ≤ 3, total extracted size limit, OCR and LibreOffice timeouts, isolated temp directories.
- Never render the HTML body of an e-mail as HTML. Convert to text.
- CSRF protection on every state-changing request (HTMX included). Session cookie: HttpOnly, Secure, SameSite=Lax. Rate-limit login. Escape every LDAP filter value.
- Containers run as non-root. Postgres and Redis are not published on host ports.
- Audit log every login, upload, publish/unpublish, download, delete, role/permission change, setting change.
- Test fixtures are synthetic. Never commit real company documents or real mail.

## 9. Documentation rules (MANDATORY)

The documentation is part of the product and you maintain it.

- Bilingual: for every topic there is `docs/en/<topic>.md` and `docs/pl/<topic>.md` with the same section structure (same headings, same order, translated). Update **both** in the same change. Each module has `docs/en/modules/<name>.md` and `docs/pl/modules/<name>.md`.
- Any change that affects behaviour, configuration, data model, permissions, search, deployment, operations or security must update the relevant docs in the same commit/PR.
- Every doc starts with the line `> Applies to: <version> · Last reviewed: <YYYY-MM-DD>` (Polish: `> Dotyczy: <wersja> · Ostatnia aktualizacja: <RRRR-MM-DD>`). Update it when you touch the file.
- Add an entry under `[Unreleased]` in `docs/en/changelog.md` and `docs/pl/changelog.md` for every user-visible or operator-visible change.
- Architectural or technology decisions go to `docs/en/decisions.md` / `docs/pl/decisions.md` as a new ADR entry. Do not rewrite old ADRs; supersede them.
- Tick the checklist in `docs/*/README.md` when a roadmap item is finished.
- `scripts/check_docs.py` (run by `make docs-check` and in CI) verifies: same file set in `en/` and `pl/`, same number of H2/H3 headings per file pair, the header line is present, `[Unreleased]` exists in both changelogs, every module in `src/baza_wiedzy/modules/` has `docs/en/modules/<name>.md` and `docs/pl/modules/<name>.md`. Keep it passing.
- Write for a future operator who has not seen the code: exact commands, expected output, what to do when it fails.

## 10. Testing rules

- New behaviour comes with tests. Bug fixes come with a regression test.
- Unit tests for extractors use small synthetic fixtures for each format, including: a text PDF, a scanned PDF, DOCX with a table, DOC, TXT in UTF-8 and Windows-1250, EML with a PDF attachment, MSG with an attachment.
- Integration tests run against real Postgres and Redis in Docker (no mocking the database).
- Permission tests: a matrix of roles × actions, including "reader cannot see drafts" and "reader cannot find drafts through search".
- A performance test generates a synthetic corpus of 20,000 documents; search p95 target < 500 ms.

## 11. Git workflow

- Feature branches, small commits, Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`).
- `main` is protected; CI (lint, types, tests, docs-check, image build) must pass.
- Releases: SemVer tags `vX.Y.Z` trigger image builds to GHCR. The tag must equal `version` in `pyproject.toml` and both changelogs must have a `## [X.Y.Z]` section (`scripts/check_release.py`). The changelog `[Unreleased]` section becomes the new version. Workflow actions are pinned to commit SHAs; only the release publish job may write packages.

## 12. Definition of done

Code works and is typed; tests pass; `make lint` and `make docs-check` pass; docs (EN + PL) and changelog are updated; migrations are included; `.env.example` is updated when variables change; permissions and audit logging are covered for any new action.

## 13. Working style

- Before touching more than one file or making a design choice, state a short plan and wait for approval if the plan deviates from this file.
- Ask when a requirement is ambiguous. Do not silently expand scope.
- Prefer boring, well-known solutions. No new dependency without a reason written in the ADR log.
- At the end of each task report: what changed, how to test it manually, which docs were updated, open questions.

## 14. Open questions (mirror of `docs/en/decisions.md`)

1. Can a Publisher publish documents uploaded by other people? Current default: only their own. A separate permission `documents.publish_any` covers the rest.
2. Retention policy for archived documents and audit log (GDPR/RODO).
3. TLS: internal CA certificate or Let's Encrypt (`TLS_MODE` is configurable).
4. Backup destination and monitoring integration (`/ready` exists; backup scripts come in Phase 6; destination decided later).
5. Should Readers also see archived documents? Default: no.
6. Is a virus scan (ClamAV) required by company policy? Default: optional `antivirus` profile, off.
7. Expected size: "about 200" at start — documents or users? Design for 20,000 documents and 100 users until confirmed.
8. Phase 2: in-process events are not persisted; choose between `processing_status` + reprocess command and an outbox table.
