# Single entry point for all project commands. POSIX shell syntax (Git Bash / Linux).
SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE      := docker compose
COMPOSE_DEV  := docker compose -f docker-compose.yml -f docker-compose.dev.yml
SERVICES     := proxy app worker ocr-worker db redis

.PHONY: help
help: ## List commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "} {printf "  %-14s %s\n", $$1, $$2}'

.PHONY: _need-docker
_need-docker:
	@command -v docker >/dev/null 2>&1 || { echo "Docker is not installed or not in PATH; this command needs it."; exit 1; }
	@test -f .env || { echo "No .env file. Create it: cp .env.example .env (then edit the passwords)."; exit 1; }

.PHONY: up
up: _need-docker ## Start all services (dev overrides)
	$(COMPOSE_DEV) up -d --build
	@$(COMPOSE_DEV) ps --format 'table {{.Service}}\t{{.Status}}'
	@echo "app/worker/ocr-worker stop after 3 failed starts in dev; see 'make logs' if one is not running."

.PHONY: down
down: _need-docker ## Stop all services
	$(COMPOSE_DEV) down

.PHONY: logs
logs: _need-docker ## Follow logs of app, worker and ocr-worker
	$(COMPOSE_DEV) logs -f app worker ocr-worker

.PHONY: build
build: _need-docker ## Build the images
	$(COMPOSE_DEV) build

.PHONY: lint
# actionlint (with shellcheck for run: scripts) from its image, pinned by digest (ADR-015).
ACTIONLINT := rhysd/actionlint:1.7.12@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667

lint: lint-workflows ## ruff, mypy, import boundaries, GitHub workflows
	uv run ruff check .
	uv run ruff format --check .
	uv run --all-extras mypy
	uv run lint-imports

.PHONY: lint-workflows
lint-workflows: ## Check .github/workflows/*.yml with actionlint (Docker); dependabot.yml: make test
	@command -v docker >/dev/null 2>&1 || { echo "Docker is needed for actionlint (workflow check). Install Docker, or run the YAML checks alone: uv run pytest tests/unit/test_github_config.py"; exit 1; }
	docker run --rm -v "$(CURDIR):/repo:ro" -w /repo $(ACTIONLINT) -no-color

.PHONY: fmt
fmt: ## Format and auto-fix
	uv run ruff check --fix .
	uv run ruff format .

.PHONY: test
test: ## Run tests (unit; integration when the stack is up)
	uv run --all-extras pytest

.PHONY: messages
messages: ## Extract UI strings and update the Polish catalog (then translate msgstr)
	uv run pybabel extract -F babel.cfg --no-location --sort-output --width 100 \
		-o src/baza_wiedzy/core/web/locale/messages.pot src
	uv run pybabel update -i src/baza_wiedzy/core/web/locale/messages.pot \
		-d src/baza_wiedzy/core/web/locale -l pl --width 100 --no-fuzzy-matching

.PHONY: docs-check
docs-check: ## Verify EN/PL documentation consistency
	uv run python scripts/check_docs.py

.PHONY: smoke
smoke: ## Build all images, start the production Compose file, migrate, require /ready 4x ok
	@command -v docker >/dev/null 2>&1 || { echo "Docker is not installed or not in PATH; this command needs it."; exit 1; }
	./scripts/smoke_test.sh

.PHONY: migrate
migrate: _need-docker ## Apply database migrations
	$(COMPOSE_DEV) run --rm app baza-wiedzy migrate

.PHONY: revision
revision: ## Create an empty migration: make revision branch=search m="message"
	@test -n "$(m)" -a -n "$(branch)" || { echo 'Usage: make revision branch=<core|module> m="message"'; exit 1; }
	uv run baza-wiedzy revision --branch "$(branch)" -m "$(m)"

.PHONY: shell
shell: _need-docker ## Shell in the app container
	$(COMPOSE_DEV) exec app bash

.PHONY: seed-samples
seed-samples: _need-docker ## Load synthetic sample documents (not available yet)
	@echo "Not implemented yet (needs upload and ingestion, Phase 2)."; exit 1

.PHONY: probki
probki: ## Synthetic sample files for a manual test: make probki DIR=~/bw-probki
	@command -v docker >/dev/null 2>&1 || { echo "Docker is not installed or not in PATH; this command needs it."; exit 1; }
	@test -n "$(DIR)" || { echo 'Usage: make probki DIR=<directory>'; exit 1; }
	mkdir -p "$(DIR)"
	docker compose -f docker-compose.test.yml run --rm --build --no-deps \
		-e OWNER="$$(id -u):$$(id -g)" -v "$$(cd "$(DIR)" && pwd):/out" tests \
		sh -c 'uv sync --frozen --all-extras --quiet && PYTHONPATH=/src/tests uv run --frozen --no-sync python scripts/make_samples.py /out && chown "$$OWNER" /out/* /out/wyszukiwanie/* /out/ocr/*'

.PHONY: relevance
TOP ?= 5
relevance: _need-docker ## Rate search on own documents (dev stack): make relevance DIR=<docs> QUERIES=<file> AS=<user> [TOP=5] [ONLY=1] [OCR=1]
	@test -n "$(DIR)" -a -n "$(QUERIES)" -a -n "$(AS)" || { echo 'Usage: make relevance DIR=<directory outside the repository> QUERIES=<file outside the repository> AS=<user name> [TOP=5]'; exit 1; }
	@dir="$$(realpath "$(DIR)")" && queries="$$(realpath "$(QUERIES)")" || exit 1; \
	for path in "$$dir" "$$queries"; do case "$$path/" in "$(CURDIR)"/*) \
	echo "Refused: $$path is inside the repository, which is public. Keep company documents and queries outside it."; exit 1;; esac; done; \
	test -d "$$dir" -a -f "$$queries" || { echo "DIR must be a directory and QUERIES a file."; exit 1; }; \
	$(COMPOSE_DEV) run --rm --no-deps -v "$$dir:/relevance/docs:ro" -v "$$queries:/relevance/queries.txt:ro" \
		-v "$(CURDIR)/scripts:/scripts:ro" app python /scripts/relevance.py \
		--docs /relevance/docs --queries /relevance/queries.txt --user "$(AS)" --top "$(TOP)" \
		$(if $(filter 1,$(ONLY)),--only-uploaded,) $(if $(filter 1,$(OCR)),--start-ocr,)

.PHONY: perf
N ?= 20000
perf: ## Search performance on N synthetic documents (manual, minutes): make perf N=20000
	@command -v docker >/dev/null 2>&1 || { echo "Docker is not installed or not in PATH; this command needs it."; exit 1; }
	mkdir -p .perf-dict perf-results
	docker compose -f docker-compose.perf.yml run --rm --build -e OWNER="$$(id -u):$$(id -g)" dict
	PERF_DOCS=$(N) OWNER="$$(id -u):$$(id -g)" docker compose -f docker-compose.perf.yml run --rm --build perf; \
	status=$$?; if [ "$(PERF_KEEP)" = "1" ]; then echo "PERF_KEEP=1: the perf database stays (docker compose -f docker-compose.perf.yml down -v removes it)"; \
	else docker compose -f docker-compose.perf.yml down -v --remove-orphans >/dev/null 2>&1; fi; exit $$status

.PHONY: perf-ocr
PAGES ?= 50
OCR_CPUS ?= 2
OCR_MEMORY_LIMIT ?= 2g
TESSDATA_BEST := https://github.com/tesseract-ocr/tessdata_best/raw/4.1.0
# The test image installs the Python packages at start (uv cache in a named volume); the OCR
# itself runs with the limits of the ocr-worker service.
PERF_OCR_RUN = docker run --rm --cpus $(OCR_CPUS) --memory $(OCR_MEMORY_LIMIT) \
	-v baza-wiedzy-perf-uv:/root/.cache/uv -v "$(CURDIR):/repo:ro" -e UV_PROJECT_ENVIRONMENT=/venv \
	--entrypoint sh
PERF_OCR_CMD = cp -r /repo /tmp/w && cd /tmp/w && uv sync --frozen --all-extras --quiet && \
	PYTHONPATH=/tmp/w/tests uv run --frozen --no-sync python scripts/perf_ocr.py --pages $(PAGES)
perf-ocr: ## OCR speed, memory, quality on synthetic scans (manual, minutes): make perf-ocr PAGES=50 [BEST=1]
	@command -v docker >/dev/null 2>&1 || { echo "Docker is not installed or not in PATH; this command needs it."; exit 1; }
	docker compose -f docker-compose.test.yml build tests
	mkdir -p perf-results
	@out="perf-results/ocr-$$(date +%Y%m%d-%H%M%S).txt"; \
	$(PERF_OCR_RUN) baza-wiedzy-worker:test -c '$(PERF_OCR_CMD)' | tee "$$out"; \
	if [ "$(BEST)" = "1" ]; then \
		mkdir -p .perf-tessdata/best; \
		for lang in pol eng osd; do test -s ".perf-tessdata/best/$$lang.traineddata" || \
			curl -fsSL -o ".perf-tessdata/best/$$lang.traineddata" "$(TESSDATA_BEST)/$$lang.traineddata" || exit 1; done; \
		sha256sum .perf-tessdata/best/*.traineddata | tee -a "$$out"; \
		$(PERF_OCR_RUN) -v "$(CURDIR)/.perf-tessdata/best:/tessdata-best:ro" baza-wiedzy-worker:test \
			-c '$(PERF_OCR_CMD) --tessdata /tessdata-best --label "best (tessdata_best 4.1.0)"' | tee -a "$$out"; \
	fi; echo "Results: $$out (not committed)"

.PHONY: test-db
test-db: ## Integration and golden search tests in Docker (throwaway DB, no host ports)
	@command -v docker >/dev/null 2>&1 || { echo "Docker is not installed or not in PATH; this command needs it."; exit 1; }
	docker compose -f docker-compose.test.yml run --rm --build tests; \
	status=$$?; docker compose -f docker-compose.test.yml down --remove-orphans >/dev/null 2>&1; exit $$status
