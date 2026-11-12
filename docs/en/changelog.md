# Changelog

> Applies to: v0.0.0 (pre-release) · Last reviewed: 2026-11-12

Format based on [Keep a Changelog](https://keepachangelog.com/). Versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added
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
