# Single entry point for all project commands. POSIX shell syntax (Git Bash / Linux).
SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE      := docker compose
COMPOSE_DEV  := docker compose -f docker-compose.yml -f docker-compose.dev.yml
SERVICES     := proxy app worker db redis

.PHONY: help
help: ## List commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "} {printf "  %-14s %s\n", $$1, $$2}'

.PHONY: _need-docker
_need-docker:
	@command -v docker >/dev/null 2>&1 || { echo "Docker is not installed or not in PATH; this command needs it."; exit 1; }
	@test -f docker-compose.yml || { echo "docker-compose.yml does not exist yet (Phase 0, stage B)."; exit 1; }

.PHONY: up
up: _need-docker ## Start all services (dev overrides)
	$(COMPOSE_DEV) up -d --build

.PHONY: down
down: _need-docker ## Stop all services
	$(COMPOSE_DEV) down

.PHONY: logs
logs: _need-docker ## Follow logs of app and worker
	$(COMPOSE_DEV) logs -f app worker

.PHONY: build
build: _need-docker ## Build the images
	$(COMPOSE_DEV) build

.PHONY: lint
lint: ## ruff, mypy, import boundaries
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run lint-imports

.PHONY: fmt
fmt: ## Format and auto-fix
	uv run ruff check --fix .
	uv run ruff format .

.PHONY: test
test: ## Run tests (unit; integration when the stack is up)
	uv run pytest

.PHONY: docs-check
docs-check: ## Verify EN/PL documentation consistency
	uv run python scripts/check_docs.py

.PHONY: migrate
migrate: _need-docker ## Apply database migrations
	$(COMPOSE_DEV) run --rm app baza-wiedzy migrate

.PHONY: revision
revision: _need-docker ## Create a migration: make revision m="message"
	@test -n "$(m)" || { echo 'Usage: make revision m="message"'; exit 1; }
	$(COMPOSE_DEV) run --rm app alembic revision --autogenerate -m "$(m)"

.PHONY: shell
shell: _need-docker ## Shell in the app container
	$(COMPOSE_DEV) exec app bash

.PHONY: seed-samples
seed-samples: _need-docker ## Load synthetic sample documents (not available yet)
	@echo "Not implemented yet (needs upload and ingestion, Phase 2)."; exit 1

.PHONY: test-db
test-db: ## Integration and golden search tests in Docker (throwaway DB, no host ports)
	@command -v docker >/dev/null 2>&1 || { echo "Docker is not installed or not in PATH; this command needs it."; exit 1; }
	docker compose -f docker-compose.test.yml run --rm --build tests; \
	status=$$?; docker compose -f docker-compose.test.yml down --remove-orphans >/dev/null 2>&1; exit $$status
