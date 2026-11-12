# Decisions, assumptions and open questions

> Applies to: v0.0.0 (pre-release) · Last reviewed: 2026-11-12

Decision log (ADR). New decisions are appended; an old decision is never rewritten, only superseded by a new entry.

## Decisions

### ADR-001 — Server-rendered UI with FastAPI + Jinja2 + HTMX
Status: accepted. A small internal tool maintained by one person. A server-rendered UI needs no separate frontend build and keeps all code in Python. Consequence: highly interactive features (for example in-browser editing) would be costly; they are out of scope.

### ADR-002 — PostgreSQL full-text search as the search engine
Status: accepted. One database for data, permissions and the index; visibility rules are applied in the same SQL; fine for tens of thousands of documents. Consequence: Polish inflection depends on a Hunspell dictionary (ADR-003). Search sits behind a `SearchBackend` interface so Meilisearch can replace it.

### ADR-003 — Three text-search configurations per text part
Status: accepted. `pl` (Hunspell + simple fallback), `english`, `simple_unaccent`; all indexed for every part, language detection is informational. Mixed-language documents and codes/names work. Cost: three tsvector columns and indexes (acceptable at this scale). Subject to the Phase 0 spike; fallbacks listed in [search.md](search.md).

### ADR-004 — Dramatiq + Redis for background jobs
Status: accepted. OCR and parsing are CPU-heavy and sync; Dramatiq's process-based workers fit. Redis holds only the queue.

### ADR-005 — pypdfium2 instead of PyMuPDF for PDF text
Status: accepted. PyMuPDF is AGPL-licensed; pypdfium2 is permissively licensed and sufficient for text extraction. Revisit only with legal approval.

### ADR-006 — OCRmyPDF + Tesseract with `pol+eng`
Status: accepted. Produces a searchable PDF and skips pages that already have text. OCR is in the MVP.

### ADR-007 — Server-side sessions in cookies, not JWT
Status: accepted. Sessions are revocable and simple with server-rendered pages and HTMX.

### ADR-008 — Active Directory through LDAPS with just-in-time users
Status: accepted. Groups map to roles in the admin UI. A local break-glass admin remains. SSO/Kerberos is a possible later step.

### ADR-009 — Explicit migration step in the update script
Status: accepted. Migrations run in a one-off container from `update.sh`, not implicitly at app start. A failed migration is a clear, single failure point. The app refuses to become ready if the schema version is behind.

### ADR-010 — Three images: app, worker, db
Status: accepted. The worker image is large (Tesseract, LibreOffice); the app image stays small. The db image carries the Hunspell dictionary.

### ADR-011 — Synchronous SQLAlchemy 2 with psycopg 3
Status: accepted. At most ~100 users; simpler code and one data-access style shared by `app` and `worker`.

### ADR-012 — Updates are performed from the host, not from the web UI
Status: accepted. Giving the web app control over Docker would be a serious security risk. The UI may show "new version available" later.

### ADR-013 — Modular monolith: always-on core plus modules `documents`, `ingestion`, `search`
Status: accepted. One codebase and one image per role (`app`, `worker`), no separate services. The core (config, db, auth, permissions, audit, admin skeleton, module registry, event bus) is always on; `documents`, `ingestion` and `search` are modules in `src/baza_wiedzy/modules/<name>/`, enabled through `ENABLED_MODULES`. Each module has a manifest (`module.py`) declaring name, version, `requires`, permissions, menu items, routers, templates, migration branch, settings schema, background tasks, CLI commands and event subscriptions. The registry validates dependencies at startup. Decisions that follow from it:
- Modules talk through `services/api.py` and synchronous in-process events (`document.version.uploaded`, `document.version.extracted`, `document.published`), never through each other's internals.
- Every module owns its tables, prefixed `doc_`, `ing_`, `srch_`; the core has no prefix. `search` owns the indexed text (`srch_parts`) so that replacing the engine touches only that module; `ingestion` publishes extracted text as an event and `search` indexes it; processing status, version metadata and e-mail metadata stay in `documents`, and `ingestion` updates them through `documents.services.api`.
- Direction for visibility: `documents.services.api` provides the SQL fragment implementing the visibility rule and `search` injects it into its queries, so visibility stays in SQL (details in Phase 3).
- One Alembic branch per module plus `core`; a module that requires another uses `depends_on`.
- Extension points: extractor registry, `SearchBackend`, login providers, file storage. Only modules inside the repository are loaded; no dynamic plugins.

Alternatives rejected: a plain package layout without manifests (boundaries erode and nothing validates what is enabled), and separate services (operating several deployments is too heavy for one maintainer). Consequences: a thin amount of framework code to maintain; events lost on a crash must be recoverable (handlers are idempotent and reprocessing republishes them); disabling a module does not remove its data.

### ADR-014 — Module boundaries are enforced by import-linter in CI
Status: accepted. A boundary that exists only in documentation is crossed sooner or later, especially in a long project built with an AI assistant. import-linter checks the import graph in `make lint` and in CI with `forbidden` contracts: the core does not import modules, and a module may import another module only through its `services/api.py` (and only a required one). `allow_indirect_imports` is on, because a public API legitimately imports its module's internals. Alternatives: ruff `banned-api` (cannot express "everything except `services/api.py`") and review only (not reliable). Consequences: adding a module requires adding a contract in `pyproject.toml`; a contract is never weakened to make a change compile, the design is changed instead.

### ADR-015 — Additional dependencies (collective entry)
Status: accepted. Nothing outside the stack in `CLAUDE.md` section 3 without an entry here.
- Phase 0, stage A: `pydantic` and `pydantic-settings` (typed configuration from environment variables, required by section 6); development only: `import-linter` (ADR-014), `ruff`, `mypy`, `pytest`, `pytest-cov`, `pre-commit` (the Quality row of the stack).
- Phase 0, stage B (added when first used): `fastapi` and `uvicorn` (web framework and its server), `jinja2` (templates, required for server-rendered pages), `python-multipart` (FastAPI form and file upload parsing), `babel` (gettext catalogs: extract, compile, Polish translation), `sqlalchemy`, `alembic`, `psycopg` (database access and migrations from the stack), `dramatiq` and `redis` (queue and Redis client, also used by the `/ready` check).

- Phase 0, stage B2: `psycopg[binary]` instead of plain `psycopg` (the `binary` extra bundles libpq, so the images need no compiler or `libpq-dev`). Test tooling, not a Python dependency: the image `ghcr.io/astral-sh/uv:0.13.7-python3.12-trixie-slim` runs `make test-db` (same `uv` as development, pinned tag).

Each version is pinned by `uv.lock`. Later phases add their own entries (for example `argon2-cffi`, `ldap3`, `pypdfium2`, `python-docx`, `extract-msg`, `charset-normalizer`, `lingua-language-detector`, `python-magic`) as they are introduced.

### ADR-016 — Polish Hunspell dictionary from Debian `hunspell-pl`, used under Apache-2.0
Status: accepted. The `pl` text-search configuration uses the SJP.PL Hunspell dictionary taken from the Debian 13 package `hunspell-pl`, version `1:25.2.3-1` (from `libreoffice-dictionaries` 25.2.3). Why: the package comes from a signed repository, has a fixed version and is already built for the base image's distribution; downloading a ZIP from a website would add a second trust path. Licence: upstream offers "GPL or LGPL or MPL or Apache-2.0 or CC-SA-1.0"; we choose the permissive **Apache-2.0**. The images are private in GHCR; the licence text and attribution ship with the files anyway (`docker/db/dictionary/LICENSE-pl_pl-dictionary.txt`, copied next to `pl_pl.dict` in the image). Integrity: the build fails unless the original files match
`pl_PL.aff` SHA-256 `7c37b9bde78054e43365b488a13859094c88bc66664b5b7a7bb073626454b38e` and
`pl_PL.dic` SHA-256 `215fd73aa47b11e7fdd2e4d655e9fe37be4acdae16ff833badcfdfce79110aad`.
Modifications: ISO-8859-2 → UTF-8, `SET UTF-8`, renamed to `pl_pl.affix` / `pl_pl.dict`. The stop-word list `polish.stop` is written by the project. Alternatives: download from sjp.pl with a pinned checksum (rejected, see above); lemmatization in Python or Meilisearch (fallbacks, not needed: see the spike result in `search.md`). Consequences: changing the package version means new checksums here, a new `db` image, a changelog entry and `baza-wiedzy reindex-search`. Each database connection holds about 27 MB for the dictionary, so connection pools stay small and fixed.

## Assumptions

- "On start about 200" (answer to the question about expected size) is read as roughly 200 documents. Design target is 20,000 documents and 100 users until confirmed.
- Server sizing is not known; resource limits are configurable.
- UI is Polish only; strings go through gettext catalogs.
- Only Polish and English documents are expected.

## Open questions

1. Can a Publisher publish documents uploaded by others? Default: no, only their own (`documents.publish_any` exists for the rest).
2. Retention: how long are archived documents and audit entries kept (GDPR/RODO)?
3. TLS certificate: internal CA or Let's Encrypt?
4. Backup destination (NAS, second server, object storage) and monitoring integration.
5. Should Readers also see archived documents? Default: no.
6. Is a virus scan (ClamAV) required by company policy? Default: optional profile, off.
7. Is the "about 200" figure documents or users?
8. To decide in Phase 2: in-process events are not persisted. If a process dies between "text extracted" and "search indexed" (or a handler fails), the event is lost. Options: rely on `processing_status` plus a reprocessing command (`baza-wiedzy reprocess`), or an outbox table. Nothing is built until Phase 2.
