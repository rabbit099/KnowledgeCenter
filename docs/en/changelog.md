# Changelog

> Applies to: v0.0.0 (pre-release) · Last reviewed: 2026-11-12

Format based on [Keep a Changelog](https://keepachangelog.com/). Versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added
- CI (`ci.yml`): lint, types, module boundaries, unit tests, docs check, integration tests with PostgreSQL and Redis, image build for `linux/amd64` and a smoke test of the production Compose file (`make smoke`). Release workflow (`release.yml`, prepared, not run): checks tag against `pyproject.toml` and changelogs, publishes four images (`app`, `worker`, `db`, `proxy`) to GHCR with commit labels (ADR-017). Offline bundle script with all six images. Redis and ClamAV pinned by digest; proxy host ports configurable (`HTTP_PORT`, `HTTPS_PORT`). The application version comes from `pyproject.toml`.
- Logs are JSON lines (`ts`, `level`, `logger`, `message`, `process`, `exc`) from `app` and the worker processes; new variable `LOG_LEVEL`.
- `baza-wiedzy migrate` applies the migrations of all modules, independent of `ENABLED_MODULES`; `/ready` compares the schema with all migration heads. Dependency policy: security-fix exception and monthly review (ADR-015).
- Worker (Dramatiq on Redis): `WORKER_PROCESSES` processes with one thread and one database connection each, heartbeat in Redis checked by `/ready` (`worker`), test job `core.ping` and the command `baza-wiedzy worker-ping`.
- Database migrations: `baza-wiedzy migrate` (and `make migrate`) applies the `core` branch (extensions `pg_trgm`, `unaccent`) and the `search` branch (Polish dictionary, configurations `pl` and `simple_unaccent`) as the non-superuser application role; `/ready` reports the schema state (`not migrated`, `outdated`, `ok`). `make revision branch=<branch> m="..."` creates a migration in a module's branch. Golden search tests now run on the real migrations.
- Web application (FastAPI): start page in Polish with the configured `APP_NAME` (Jinja2, HTMX served locally, gettext catalog `pl`), `/health`, `/ready` (database and Redis checks; JSON, 503 when a check fails) and an HTMX status fragment. New command `make messages`. Dependency resolution only picks releases older than a fixed date (`exclude-newer`).
- Redis requires a password (`REDIS_PASSWORD`, new variable). The file volume is named `storage` and mounted at `/data/storage`. In development `app` and `worker` stop after 3 failed starts instead of restarting forever.
- Docker Compose stack (`docker-compose.yml`, `docker-compose.dev.yml`): proxy (Caddy, non-root, TLS modes `internal`/`files`/`acme`), app, worker, db, redis, optional `antivirus` profile (ClamAV); log rotation, memory limits, explicit `max_connections`; app and worker images (non-root; worker with Tesseract `pol`+`eng`, OCRmyPDF, LibreOffice Writer); the `db` image creates a non-superuser application role on first start. New variables in `.env.example`.
- Polish dictionary spike done: Hunspell is sufficient (results, measured memory and gaps in `search.md`, ADR-016). Golden search tests in `tests/search_golden/` and `make test-db`, which runs them against a throwaway database in Docker without publishing a port.
- Database image `docker/db` (PostgreSQL 16.15 on Debian 13) with the Polish Hunspell dictionary (`pl_pl.dict`, `pl_pl.affix`, UTF-8), the project's `polish.stop` list and the dictionary licence file; `docker/db/spike.sql` is a diagnostic script for the dictionary.
- Repository skeleton: Python 3.12 project managed with `uv`, `Makefile` as the single command entry point, pre-commit (ruff, mypy), `.env.example`, LF line endings.
- `scripts/check_docs.py` and `make docs-check`: verify that the English and Polish documentation (including `modules/`) have the same files, headings and header lines, that both changelogs have `[Unreleased]`, and that every module in `src/baza_wiedzy/modules/` has a page in `docs/en/modules/` and `docs/pl/modules/`.
- Modular structure: always-on core plus modules `documents`, `ingestion`, `search` (empty manifests for now) selected with `ENABLED_MODULES`; dependency check at startup with readable errors; permission catalog assembled from module manifests; in-process event bus; module boundaries checked by import-linter in `make lint`.
- Documentation of the modular design: ADR-013 to ADR-015, module pages in `docs/*/modules/`, new sections in architecture, data model, deployment and operations.
- Planned table names now carry a module prefix (`doc_*`, `srch_*`, `ing_*`); the indexed text table is `srch_parts` (was `document_parts`). Documentation only; no tables exist yet.
- Project guide `CLAUDE.md` and bilingual documentation set (architecture, data model, search, ingestion and OCR, authentication and permissions, deployment and operations, decisions, roadmap).
