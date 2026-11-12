# Baza Wiedzy

Internal company knowledge base: upload documents (PDF, DOCX, DOC, TXT, EML, MSG), extract and index their text (OCR for scans), search by phrase with highlighted snippets. Runs in Docker Compose.

The application is a modular monolith: an always-on core plus the modules `documents`, `ingestion` and `search`, enabled through `ENABLED_MODULES`.

- Project rules for contributors and Claude Code: [CLAUDE.md](CLAUDE.md)
- Documentation: [English](docs/en/README.md) · [Polski](docs/pl/README.md)

## Quick start (development)

Requires `git`, `make` and [`uv`](https://docs.astral.sh/uv/) (it installs Python 3.12 by itself).

```
cp .env.example .env
uv sync
make lint
make test
make docs-check
```

With Docker (Compose plugin):

```
make build && make up && make migrate
curl -k https://localhost/ready   # {"status":"ok", ... 4 checks "ok"}
make test-db                       # integration and golden search tests
make smoke                         # production Compose file with fresh images
```

The browser shows a certificate warning for `https://localhost` (Caddy's internal CA). All commands go through the `Makefile`; run `make help` for the list. Details: [deployment and operations](docs/en/deployment-operations.md).
