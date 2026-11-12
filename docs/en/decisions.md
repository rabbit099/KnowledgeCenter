# Decisions, assumptions and open questions

> Applies to: v0.1.0 · Last reviewed: 2026-11-12

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
Status: superseded by ADR-017. The worker image is large (Tesseract, LibreOffice); the app image stays small. The db image carries the Hunspell dictionary.

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
- Phase 0, stage B3: `ocrmypdf` as the optional extra `worker` (installed only in the worker image; OCR engine from the stack, used as a Python library). Base images, pinned to exact tags: `python:3.12.16-slim-trixie`, `ghcr.io/astral-sh/uv:0.13.7-python3.12-trixie-slim` (build stage), `caddy:2.10.2-alpine`, `redis:8.2.3-alpine`, `clamav/clamav:1.4.3`.
- Phase 0, stage B4: `fastapi` and `uvicorn` (web framework and ASGI server), `jinja2` (server-rendered templates), `babel` (gettext extraction and the Polish catalog, compiled in memory at startup), `sqlalchemy` (engine and pool, used by `/ready` now and by models later), `redis` (client for the `/ready` check; Dramatiq uses it too). Development only: `httpx` (required by FastAPI's `TestClient`). Front-end asset, not a Python package: HTMX 2.0.11 (0BSD), downloaded from the npm registry with its published integrity hash checked, vendored in `src/baza_wiedzy/core/web/static/vendor/`. `python-multipart` is postponed until uploads exist (Phase 2).
- Resolution policy: `[tool.uv] exclude-newer` in `pyproject.toml` makes `uv lock` pick only releases published before a fixed date, set at least two weeks back, so a freshly published (possibly compromised or broken) release is never picked up automatically. Moving the date forward is a deliberate upgrade step.
- Exception for security fixes: when a dependency publishes a security fix, it is taken without waiting two weeks, either by moving `exclude-newer` forward (if the other upgrades it brings are acceptable) or by unblocking just that package with `[tool.uv] exclude-newer-package = { <name> = "<date>" }` and `uv lock --upgrade-package <name>`. The changelog entry names the advisory. The per-package exception is removed at the next monthly review.
- Monthly dependency review (first working week of the month): move `exclude-newer` to 14 days before the review, `uv lock --upgrade`, read `git diff uv.lock`, run `make lint test test-db smoke`, check base image tags (Python, Caddy, Redis, PostgreSQL, uv, ClamAV) and the Debian `hunspell-pl` version, commit as `chore(deps): monthly review YYYY-MM` with a changelog entry. A changed `hunspell-pl` means new checksums in ADR-016 and `reindex-search`.
- Phase 0, stage B5: `alembic` (migrations from the stack; one branch per module).
- Phase 0, stage B6: `dramatiq[redis]` (job queue from the stack; the `redis` extra pulls the Redis client already listed).
- Phase 0, stage B7 (CI, not Python packages): GitHub Actions `actions/checkout` v7.0.1, `astral-sh/setup-uv` v10.3.2, `docker/setup-buildx-action` v4.4.1, `docker/login-action` v4.7.0, `docker/build-push-action` v7.4.0, each pinned to its commit SHA (older than `exclude-newer`); workflows were checked with `actionlint` and the shell scripts with `shellcheck` (both run from their Docker images, not installed in the project).
- Dependabot (`.github/dependabot.yml`), consistent with the rules above: `github-actions` (updates the pinned commit SHAs together with the version comment), `docker` (base images in `docker/*/Dockerfile`) and `docker-compose` (`redis`, `clamav` tag and digest), all monthly, grouped, with a 14-day `cooldown` that mirrors `exclude-newer`. For Python (`uv`) routine version pull requests are switched off (`open-pull-requests-limit: 0`): `exclude-newer` would block them and versions move in the monthly review; Dependabot security updates (enable "Dependabot alerts" and "Dependabot security updates" in the repository settings) still open pull requests, handled with the security-fix exception (`exclude-newer-package`). Every Dependabot pull request runs the full CI; none is merged without it. The monthly review starts by merging or closing the open Dependabot pull requests. Dependabot does not track the Debian package `hunspell-pl`; the review checks it by hand.

Each version is pinned by `uv.lock`. Later phases add their own entries (for example `argon2-cffi`, `ldap3`, `pypdfium2`, `python-docx`, `extract-msg`, `charset-normalizer`, `lingua-language-detector`, `python-magic`) as they are introduced.

### ADR-016 — Polish Hunspell dictionary from Debian `hunspell-pl`, used under Apache-2.0
Status: accepted. The `pl` text-search configuration uses the SJP.PL Hunspell dictionary taken from the Debian 13 package `hunspell-pl`, version `1:25.2.3-1` (from `libreoffice-dictionaries` 25.2.3). Why: the package comes from a signed repository, has a fixed version and is already built for the base image's distribution; downloading a ZIP from a website would add a second trust path. Licence: upstream offers "GPL or LGPL or MPL or Apache-2.0 or CC-SA-1.0"; we choose the permissive **Apache-2.0**. The images are private in GHCR; the licence text and attribution ship with the files anyway (`docker/db/dictionary/LICENSE-pl_pl-dictionary.txt`, copied next to `pl_pl.dict` in the image). Integrity: the build fails unless the original files match
`pl_PL.aff` SHA-256 `7c37b9bde78054e43365b488a13859094c88bc66664b5b7a7bb073626454b38e` and
`pl_PL.dic` SHA-256 `215fd73aa47b11e7fdd2e4d655e9fe37be4acdae16ff833badcfdfce79110aad`.
Modifications: ISO-8859-2 → UTF-8, `SET UTF-8`, renamed to `pl_pl.affix` / `pl_pl.dict`. The stop-word list `polish.stop` is written by the project. Alternatives: download from sjp.pl with a pinned checksum (rejected, see above); lemmatization in Python or Meilisearch (fallbacks, not needed: see the spike result in `search.md`). Consequences: changing the package version means new checksums here, a new `db` image, a changelog entry and `baza-wiedzy reindex-search`. Each database connection holds about 27 MB for the dictionary, so connection pools stay small and fixed.

### ADR-017 — Four images: app, worker, db, proxy (supersedes ADR-010)
Status: accepted. The proxy needs its own image: the official Caddy image cannot run as a non-root user with writable certificate storage (`/data` belongs to root) and its binary carries a file capability that `cap_drop: ALL` refuses to execute. `docker/caddy/Dockerfile` adds a `caddy` user, owns `/data` and `/config`, drops the file capability and listens on 8080/8443. Building it on the server (first approach in Phase 0 B3) would need Docker Hub access and break the offline procedure, so the release workflow publishes it like the others: `baza-wiedzy-{app,worker,db,proxy}:X.Y.Z` in GHCR, `linux/amd64`, labelled with version and commit SHA. Third-party images that are not rebuilt (`redis`, optional `clamav`) are pinned by tag and digest in `docker-compose.yml`. Consequences: the server needs no build tooling and no source; the offline bundle (`scripts/offline_bundle.sh`) is generated from `docker-compose.yml` and contains all six images; every base-image update of Caddy goes through a release.

## Assumptions

- "On start about 200" (answer to the question about expected size) is read as roughly 200 documents. Design target is 20,000 documents and 100 users until confirmed.
- Server sizing is not known; resource limits are configurable.
- UI is Polish only; strings go through gettext catalogs.
- Only Polish and English documents are expected.

## Open questions

1. ~~Can a Publisher publish documents uploaded by others?~~ Decided 2026-11-12: no. The Publisher role publishes only its own documents; `documents.publish_any` is not part of Publisher (an admin can grant it through a custom role).
2. Retention: how long are archived documents and audit entries kept (GDPR/RODO)?
3. TLS certificate: internal CA or Let's Encrypt? Working assumption (2026-11-12): internal company CA (`TLS_MODE=files`, or `internal` for tests); Let's Encrypt (`acme`) stays a supported variant. To be confirmed with the domain administrator.
4. Backup destination (NAS, second server, object storage) and monitoring integration.
5. ~~Should Readers also see archived documents?~~ Decided 2026-11-12: no. Readers see only published documents.
6. Is a virus scan (ClamAV) required by company policy? Default: optional profile, off.
7. Is the "about 200" figure documents or users?
8. To decide in Phase 2: in-process events are not persisted. If a process dies between "text extracted" and "search indexed" (or a handler fails), the event is lost. Options: rely on `processing_status` plus a reprocessing command (`baza-wiedzy reprocess`), or an outbox table. Nothing is built until Phase 2.
