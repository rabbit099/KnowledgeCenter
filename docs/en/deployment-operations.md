# Deployment and operations

> Applies to: v0.2.1 · Last reviewed: 2026-11-17

Everything an operator needs: installing, configuring, updating, rolling back, backing up, restoring, monitoring.

## Requirements

- Linux server with Docker Engine and the Docker Compose plugin.
- Access to `ghcr.io` (or the offline procedure below).
- A DNS name for the application and a TLS certificate (internal CA or Let's Encrypt — to be decided).
- Reachability of the domain controller over LDAPS (port 636) and a service account for LDAP reads.
- Disk space: for documents (originals + derived copies + database). Plan 3× the size of the uploaded files as a starting point.

## Layout on the server

```
/srv/baza-wiedzy/
├── docker-compose.yml
├── .env                 # configuration and secrets (chmod 600)
├── scripts/             # update.sh, rollback.sh, backup.sh, restore.sh
├── secrets/tls/         # cert.pem and key.pem, only with TLS_MODE=files
├── secrets/ldap/        # ca.pem and bind_password for Active Directory (owner 10001, mode 400)
├── logs/                # update.log, backup.log
└── backups/             # created by backup.sh
```

Data lives in Docker named volumes of the Compose project `baza-wiedzy`: `pgdata` (database), `storage` (uploaded and derived files, mounted at `/data/storage` in `app` and `worker`), `redis-data` (queue), `caddy-data` and `caddy-config` (certificates, including Caddy's internal CA), `clamav-db` (only with the `antivirus` profile). List them with `docker volume ls --filter name=baza-wiedzy`. `docker compose down` keeps them; `docker compose down -v` **deletes all data**.

## Configuration

All settings are environment variables in `.env`; `.env.example` in the repository documents every variable and is the source of truth. Key variables:

| Variable | Meaning |
|---|---|
| `APP_NAME` | Display name (default "Baza Wiedzy") |
| `APP_VERSION` | Tag of the project images to run, for example `0.1.0` (required by `docker-compose.yml`). It only selects images: the application reports its own version from the installed package (`version` in `pyproject.toml`), shown on `/health`, `/ready` and in the page footer |
| `LOG_LEVEL` | `DEBUG`, `INFO` (default), `WARNING`, `ERROR`; logs are JSON lines (see Monitoring) |
| `ENABLED_MODULES` | Optional modules to run, comma-separated (default `documents,ingestion,search`); the core is always on |
| `PUBLIC_HOSTNAME` | DNS name used by Caddy |
| `TLS_MODE` | `internal`, `files` or `acme` |
| `SECRET_KEY` | Signs CSRF tokens; at least 32 characters, the application refuses to start otherwise. Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `SESSION_IDLE_MINUTES`, `SESSION_ABSOLUTE_HOURS` | Session idle limit (120) and absolute lifetime (12) |
| `LOGIN_WINDOW_MINUTES`, `LOGIN_LOCKOUT_MINUTES`, `LOGIN_MAX_FAILURES_ACCOUNT`, `LOGIN_MAX_FAILURES_ACCOUNT_IP`, `LOGIN_MAX_FAILURES_IP` | Failed-login limits ([auth-and-permissions.md](auth-and-permissions.md)) |
| `DOCKER_SUBNET`, `PROXY_IP` | Fixed Docker network of the stack (default `172.30.70.0/24`) and the proxy's address inside it (`172.30.70.10`); the app trusts `X-Forwarded-For` only from `PROXY_IP` |
| `HTTP_PORT`, `HTTPS_PORT` | Host ports of the proxy (default 80, 443) |
| `IMAGE_PREFIX` | Registry and name prefix of the images, for example `ghcr.io/<owner>/baza-wiedzy` |
| `BACKEND_SUBNET` | Internal network of `app`, `worker`, `db`, `redis` without internet access (default `172.30.72.0/24`; change it if it collides with a company network, ADR-024) |
| `ACME_EMAIL`, `MAX_REQUEST_MB` | Let's Encrypt contact (`TLS_MODE=acme`); largest request body accepted by the proxy (default 51, keep it `MAX_UPLOAD_MB` + 1) |
| `MAX_UPLOAD_MB`, `STORAGE_DIR` | Largest uploaded file (default 50 MB); storage volume path in the containers (default `/data/storage`) |
| `INGEST_MAX_ATTEMPTS`, `INGEST_RETRY_MIN_SECONDS`, `INGEST_RETRY_MAX_SECONDS`, `INGEST_TIME_LIMIT_SECONDS`, `INGEST_STALE_MINUTES` | Processing in the worker: attempts for transient errors (3), delay between them (30 s doubling up to 600 s), time limit of one attempt (900 s), age after which a version still "processing" counts as interrupted (30 min, keep it above the time limit) |
| `INGEST_MAX_DEPTH`, `INGEST_MAX_PARTS`, `INGEST_MAX_TEXT_MB`, `LANGUAGE_MIN_LETTERS` | Extraction limits per file: e-mail attachment depth (3), parts (2000), text (50 MB); letters needed before a language is detected (40) |
| `OUTBOX_RETENTION_DAYS`, `SCAN_HOOK` | Days a delivered event stays in `ing_outbox` (7); virus scan step before extraction (`none`, the only value for now) |
| `POSTGRES_PASSWORD` | Password of the PostgreSQL superuser; used only inside the `db` container |
| `APP_DB_USER` / `APP_DB_PASSWORD` / `APP_DB_NAME` | Non-superuser that owns the application database; the application and migrations use it. `DATABASE_URL` is built from these in `docker-compose.yml` |
| `POSTGRES_MAX_CONNECTIONS`, `POSTGRES_SHARED_BUFFERS` | Explicit connection limit and shared buffers (see below) |
| `DB_MEMORY_LIMIT`, `APP_MEMORY_LIMIT`, `WORKER_MEMORY_LIMIT`, `REDIS_MEMORY_LIMIT`, `CLAMAV_MEMORY_LIMIT` | Container memory limits |
| `WORKER_PROCESSES` | Worker processes (default 2; one thread and one database connection each; not more than CPU cores). Memory measured with 2 processes: 173 MiB idle, below 0.5 GB under load; keep `WORKER_MEMORY_LIMIT` (default 2g) at least 1.4 GB + 0.6 GB per process ([ingestion-and-ocr.md](ingestion-and-ocr.md#native-code-in-child-processes)) |
| `INGEST_MAX_PAGES`, `OCR_MIN_CHARS`, `INGEST_CHILD_MEMORY_MB`, `INGEST_CHILD_TIMEOUT_SECONDS`, `LIBREOFFICE_MEMORY_MB`, `LIBREOFFICE_TIMEOUT_SECONDS` | PDF page limit (2000) and the threshold for "needs OCR" pages (25 characters); memory and time limits of the PDF child process (1024 MB, 300 s) and of LibreOffice (1024 MB, 120 s) |
| `REDIS_PASSWORD` | Password required by Redis (`requirepass`); URL-safe characters only. `REDIS_URL` is built from it in `docker-compose.yml` |
| `MAX_UPLOAD_MB` and other limits | See [ingestion-and-ocr.md](ingestion-and-ocr.md) |
| `LDAP_*` | Active Directory login, off by default (`LDAP_ENABLED=false`). `./secrets/ldap/` is mounted read-only at `/run/secrets/ldap/` in `app` for the CA certificate and the service-account password file. Details, normalization of names and the test procedure for a real AD: [auth-and-permissions.md](auth-and-permissions.md#active-directory-ldap-over-tls) |

`ENABLED_MODULES` must have the same value for `app` and `worker` (both read the same `.env`). At startup the application checks module dependencies and refuses to start with a message such as `module 'search' requires 'documents', which is not enabled`. `baza-wiedzy migrate` applies the migrations of the core and of **every** module, independent of `ENABLED_MODULES`: the schema is always complete, so enabling a module later needs no migration step and `/ready` compares the database with all migration heads. Disabling a module does not delete its tables or data.

Database user and privileges: the application and `baza-wiedzy migrate` connect as one database user that is **not** a superuser but **owns the application database**. That is enough for everything the migrations do: `CREATE EXTENSION pg_trgm` and `CREATE EXTENSION unaccent` (both are "trusted" extensions in PostgreSQL 13+, so the database owner may create them; the extension files are already in the `db` image), and `CREATE TEXT SEARCH DICTIONARY` / `CREATE TEXT SEARCH CONFIGURATION` (need `CREATE` on schema `public`, which the database owner has in PostgreSQL 15+). If the database is created by someone else, grant: `GRANT CREATE ON DATABASE <db> TO <user>; GRANT CREATE ON SCHEMA public TO <user>;`. `make test-db` runs the search objects as such a non-superuser, so a regression shows up in tests. On the first start of an empty `pgdata` volume, the `db` image creates this user and database from `APP_DB_USER`, `APP_DB_PASSWORD` and `APP_DB_NAME` (`docker/db/initdb/10-app-role.sh`). Changing these variables later does **not** change an existing database; change the password with `ALTER ROLE` as `postgres` and update `.env` at the same time.

**Network collisions:** the stack uses the fixed subnet `DOCKER_SUBNET` (default `172.30.70.0/24`). If the company network or a VPN route uses the same range, hosts in it become unreachable from the server (traffic goes to the Docker bridge instead), and `docker compose up` may fail with `Pool overlaps with other one on this address space`. Before the first installation check with the network administrator, and compare with `ip route` on the server. To change it, set both `DOCKER_SUBNET` and `PROXY_IP` (inside the new subnet) in `.env`, then `docker compose down && docker compose up -d` (the network is recreated; volumes are kept).

### Database connections and memory

Every PostgreSQL connection loads the Polish dictionary on first use and keeps it: about 27–30 MB per connection (measured in the spike, see [search.md](search.md)). The connection limit is therefore set explicitly and the `db` memory limit is derived from it:

- Connection budget: app 5 + 5 overflow per app process, worker 1 per worker process, plus a few for migrations, backups and manual `psql`. With 2 app processes and 2 worker processes: 2 × 10 + 2 × 1 + 6 = 28, so the default `POSTGRES_MAX_CONNECTIONS=40` leaves room.
- Memory: `DB_MEMORY_LIMIT` ≥ `POSTGRES_SHARED_BUFFERS` + `POSTGRES_MAX_CONNECTIONS` × 35 MB + 300 MB. Defaults: 256 MB + 40 × 35 MB + 300 MB ≈ 1.95 GB → `DB_MEMORY_LIMIT=2g`.
- If you raise `POSTGRES_MAX_CONNECTIONS`, raise `DB_MEMORY_LIMIT` by 35 MB per extra connection. Otherwise the kernel may kill PostgreSQL (`docker compose ps` shows `db` restarting; `docker inspect baza-wiedzy-db-1 --format '{{.State.OOMKilled}}'` prints `true`).
- Check the current values: `docker compose exec db psql -U postgres -c 'SHOW max_connections' -c 'SELECT count(*) FROM pg_stat_activity'`.

## First installation

1. Copy `docker-compose.yml`, `.env.example` and `scripts/` to `/srv/baza-wiedzy/`; rename `.env.example` to `.env` and fill it in; `chmod 600 .env`.
2. Log in to the registry. GHCR packages are **private by default**, so the server needs a token: create a GitHub personal access token (classic) with only the `read:packages` scope, ideally for a dedicated account with read access to the repository, then on the server: `echo "<token>" | docker login ghcr.io -u <github-user> --password-stdin` (expected: `Login Succeeded`; the credentials are stored in `~/.docker/config.json` of the user running Docker, protect that file). Without it `docker compose pull` fails with `denied` or `unauthorized`. Renew the token before it expires; the same applies to the machine that builds the offline bundle.
3. Set `APP_VERSION` and `IMAGE_PREFIX`, then start: `docker compose pull && docker compose up -d`. All four project images (`app`, `worker`, `db`, `proxy`) come from GHCR; nothing is built on the server.
4. Run migrations: `docker compose run --rm app baza-wiedzy migrate`. The worker started in step 3 waits for this: until the schema is at all migration heads it logs `worker startup: waiting for database migrations (schema: not migrated); run baza-wiedzy migrate` (once a minute) and runs its startup work (recovery, outbox) only afterwards. The same applies in development to `make up` followed by `make migrate`.
5. Create the first admin: `docker compose exec app baza-wiedzy create-admin bw-admin` (asks for the password twice; 14-256 characters), then log in at `https://<PUBLIC_HOSTNAME>/login`.
6. Check `https://<PUBLIC_HOSTNAME>/ready` returns OK.
7. Log in as the admin, configure AD group mapping, test an AD login.
8. Schedule backups (see below).

## Releases

- Versioning: SemVer. A Git tag `vX.Y.Z` on `main` triggers `.github/workflows/release.yml`. First release: `v0.1.0` (2026-11-12).
- Before tagging: set `version = "X.Y.Z"` in `pyproject.toml` (the application reads its version from there), move `[Unreleased]` in both changelogs under `## [X.Y.Z] - YYYY-MM-DD`, add a new empty `[Unreleased]`, commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`. Check locally first: `uv run python scripts/check_release.py vX.Y.Z` must print `Release vX.Y.Z matches pyproject.toml and both changelogs.`
- The workflow (1) refuses the release if the tagged commit is not on `main` (`git merge-base --is-ancestor`; tag the commit of `main` after the release PR is merged, not a commit of a feature branch), if the tag differs from `pyproject.toml` or if a changelog has no `## [X.Y.Z]` section, (2) runs the complete CI (including the smoke test), (3) builds and pushes four images for `linux/amd64`: `ghcr.io/<owner>/baza-wiedzy-app`, `-worker`, `-db`, `-proxy`, all tagged `X.Y.Z` (no `latest`). Each image carries the labels `org.opencontainers.image.version`, `.revision` (the commit SHA), `.source` and `.created`. The run summary lists every image with its digest; record them in the release notes.
- Check what a server runs: `docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' <image>`.
- Workflow security: actions are pinned to commit SHAs; the default token is read-only (`contents: read`), only the publish job of `release.yml` gets `packages: write`; CI uses no secrets, so pull requests from forks have nothing to leak.

CI (`.github/workflows/ci.yml`, on pushes to `main` and on pull requests) runs three jobs: `quality` (`make lint`: ruff, mypy, import-linter; `make test`; `make docs-check`), `integration` (`make test-db`: PostgreSQL from the project image and Redis) and `images` (`make smoke`: builds the four images for `linux/amd64`, starts `docker-compose.yml`, migrates, requires `/ready` to report 4 × `ok`, then creates a break-glass admin and checks through the proxy: wrong password 401, login, admin panel and audit log 200, logout). Run the same locally before pushing.

## Updating

```
cd /srv/baza-wiedzy
./scripts/update.sh 1.2.0
```

What `update.sh` does:
1. Pre-checks: Docker running, free disk space, the new images exist in the registry, `.env` contains all variables listed in the new `.env.example` (otherwise it stops and lists the missing ones).
2. Runs `backup.sh` (database + files).
3. Pulls the new images.
4. Stops `worker` so no jobs run during the migration (a worker that starts before the migration has finished waits for it before its startup work, see First installation).
5. Runs migrations in a one-off container (`baza-wiedzy migrate`).
6. Sets `APP_VERSION` in `.env` and starts everything (`docker compose up -d`).
7. Waits for `/ready` (up to 120 s).
8. On failure: automatic rollback to the previous image version and a message explaining whether the database needs to be restored.
9. Appends the result to `logs/update.log`.

Migrations follow expand/contract, so the previous app version normally still works with the new schema. If a release contains a migration that is not backward compatible, the release notes say so and rollback requires restoring the backup.

### Rollback

```
./scripts/rollback.sh 1.1.0
```

Switches `APP_VERSION` back and restarts. If the schema changed incompatibly, restore the pre-update backup (see Restore).

### Offline server

On a machine with internet access and `docker login ghcr.io` (token with `read:packages`, see First installation), from a checkout of the release tag:

```
git checkout v1.2.0
./scripts/offline_bundle.sh 1.2.0 ghcr.io/<owner>/baza-wiedzy /tmp/bundle
```

The script takes the image list from `docker-compose.yml` (with the `antivirus` profile), pulls everything for `linux/amd64` and saves it into one file. For version `X.Y.Z` the bundle contains **all** images the server needs:

| Image | Reference |
|---|---|
| app | `ghcr.io/<owner>/baza-wiedzy-app:X.Y.Z` (digest in the release summary) |
| worker | `ghcr.io/<owner>/baza-wiedzy-worker:X.Y.Z` |
| db | `ghcr.io/<owner>/baza-wiedzy-db:X.Y.Z` |
| proxy | `ghcr.io/<owner>/baza-wiedzy-proxy:X.Y.Z` |
| redis | `redis:8.2.3-alpine@sha256:08ad0b1d280850169a790dba1393ff7a90aef951fc19632cf4d3ce4f78e679ba` |
| clamav (optional profile) | `clamav/clamav:1.4.3@sha256:75fb5fd95fcbe1d7e6d240c369c1572b686ee2c95949d1042b5148de8eddebb4` |

Output: `baza-wiedzy-images-X.Y.Z.tar` (about 0.7 GB), `.tar.sha256` and `baza-wiedzy-images-X.Y.Z.txt` (each reference with its image ID). Copy the three files and the release's `docker-compose.yml`, `.env.example` and `scripts/` to the server, then:

```
sha256sum -c baza-wiedzy-images-X.Y.Z.tar.sha256     # expect: OK
docker load -i baza-wiedzy-images-X.Y.Z.tar          # lists 6 "Loaded image" lines
docker compose up -d --pull never
```

Later, `update.sh` with `SKIP_PULL=1` (Phase 6). Requirement: Docker Engine with the containerd image store (default for new installations of Docker Engine 29+; check: `docker info -f '{{.DriverStatus}}'` shows `io.containerd.snapshotter.v1`). The digest-pinned `redis` and `clamav` references are resolved from the loaded images only with that store; with the older store `up --pull never` fails with `No such image` for them.

### After changing search configuration

Run `docker compose exec app baza-wiedzy reindex-search` (see [search.md](search.md)).

This applies to every change of the Polish dictionary, the stop-word list or a text-search configuration, including a new `db` image that ships a different `hunspell-pl` version or `polish.stop` (the changelog says so when it happens). Existing `tsv_*` values were computed with the old dictionary and will not match queries processed with the new one until reindexed. Restarting `db` also drops all connections, so every connection loads the new dictionary on first use.

## Backup

`scripts/backup.sh` (Phase 6) creates `/srv/baza-wiedzy/backups/YYYY-MM-DD_HHMMSS/` containing:
- `db.dump` — `docker compose exec -T db pg_dump -Fc -U postgres <APP_DB_NAME>` (consistent snapshot while the application runs)
- `storage.tar` — the `storage` volume (original and derived files), read through a short-lived container: `docker run --rm -v baza-wiedzy_storage:/data/storage:ro -v "$BACKUP_DIR":/backup <app image> tar -C /data -cf /backup/storage.tar storage`
- `env.backup` — copy of `.env` (contains secrets; protect the backup location)
- `SHA256SUMS`

Default retention: 7 daily, 4 weekly, 6 monthly (`BACKUP_KEEP_*`). Copying backups off the server (NAS, other server, object storage) is a hook: set `BACKUP_REMOTE_CMD` to a command that receives the backup directory path. The destination is decided later; until then backups live only on the same server and that is **not** a sufficient backup.

Schedule (cron, daily at night):

```
15 2 * * * /srv/baza-wiedzy/scripts/backup.sh >> /srv/baza-wiedzy/logs/backup.log 2>&1
```

Docker volumes are not copied directly (copying `pgdata` of a running database is not consistent); the database goes through `pg_dump`, the files through `tar`. Redis holds only the job queue; it needs no backup (unfinished jobs are re-queued with `baza-wiedzy reprocess --pending`).

## Restore

```
./scripts/restore.sh /srv/baza-wiedzy/backups/2026-11-12_021500
```

It stops `app` and `worker`, restores the database (`pg_restore --clean --if-exists`), restores the files, starts the services, and verifies counts of documents and files.

**Restore test:** once a month, restore the latest backup on a spare machine or a temporary Compose project and run a few searches. A backup that was never restored is not a backup.

## Monitoring

| Endpoint | Meaning |
|---|---|
| `/health` | Process is alive (no dependency checks) |
| `/ready` | Database reachable, Redis reachable, worker heartbeat recent, schema migrated for the enabled modules. JSON; HTTP 200 when every check is `ok`, otherwise 503 |

Example of a healthy answer (`curl -k https://<PUBLIC_HOSTNAME>/ready`):

```
{"status":"ok","version":"1.0.0","checks":{"database":"ok","redis":"ok","schema":"ok","worker":"ok"}}
```

| Check | Value other than `ok` | Meaning and action |
|---|---|---|
| `database` | `unreachable` | `docker compose ps db`, `docker compose logs db`; check `APP_DB_PASSWORD` |
| `redis` | `unreachable` / `authentication failed` | `docker compose ps redis`; `REDIS_PASSWORD` in `.env` differs from the one Redis started with: `docker compose up -d redis app worker` |
| `schema` | `not migrated` / `outdated` / `unknown` | Run `docker compose run --rm app baza-wiedzy migrate`. `ok (schema newer than application)` is normal for a moment during a rollback |
| `worker` | `no heartbeat` | No worker process wrote its heartbeat in the last 60 s: `docker compose ps worker`, `docker compose logs worker` |

Check the queue end to end: `docker compose exec worker baza-wiedzy worker-ping` sends a test job and prints `Worker answered in 0.6 s.` (exit code 0), or `No answer from a worker within 10.0 s.` (exit code 1).

Zabbix: an HTTP check on `/ready` (expect 200) plus a trigger on the response time. Metrics endpoint and queue length are optional later additions.

Logs are JSON lines, one object per line: `{"ts": "2026-11-12T11:10:39.174+00:00", "level": "INFO", "logger": "uvicorn.access", "message": "10.0.0.5:0 - \"GET /ready HTTP/1.1\" 200", "process": 8}`; errors add `exc` with the traceback. `app` writes to stdout, worker processes to stderr. Exception: the Dramatiq main process of `worker` prints its few start and stop lines in Dramatiq's own text format (`[2026-11-12 11:10:33,910] [PID 1] [MainThread] [dramatiq.MainProcess] [INFO] ...`). `LOG_LEVEL` sets the level (default `INFO`). Compose sets Docker log rotation (`max-size 10m`, `max-file 5`). Read them with `docker compose logs -f app worker`; filter with `jq`, for example `docker compose logs --no-log-prefix app | jq -R 'fromjson? | select(.level=="ERROR")'`.

## Developer commands

Everything goes through the `Makefile` (run from Git Bash on Windows or from a Linux shell). Python 3.12 and the virtual environment are managed by `uv`; run `uv sync --all-extras` once (the `worker` extra holds the document parsers the tests and type checks need; `make lint` and `make test` add it themselves).

| Command | What it does |
|---|---|
| `make help` | Lists all commands |
| `make lint` | `ruff`, `mypy` and the module boundary check (`lint-imports`) |
| `make fmt` | Formats and auto-fixes with `ruff` |
| `make test` | Runs the tests with `pytest` (integration and golden search tests are skipped) |
| `make smoke` | Smoke test of the production Compose file: builds the four images (`linux/amd64`), starts `docker-compose.yml` as project `baza-wiedzy-smoke` on ports 18080/18443 with generated secrets, migrates, requires `/ready` = 4 × `ok`, then creates the break-glass admin `smoke-admin` with a generated password and checks through the proxy: admin panel without a session 303, wrong password 401, login 303, `/admin/users` and `/admin/audit` 200, upload of a small TXT file (204) and waiting until the worker has processed it ("gotowe", at most 2 minutes), the parts page 200, logout 303. Removes everything at the end. Expected end: `==> Smoke test passed: /ready 4 x ok, login, admin panel, upload processed to "ready", logout` (about 1 minute with cached layers) |
| `make test-db` | Runs the integration and golden search tests in Docker: a throwaway PostgreSQL from `docker/db` on tmpfs and a one-off test runner on a private Compose network (`docker-compose.test.yml`); no port is published. The runner is the target `test` of the worker image (LibreOffice, Tesseract; ADR-023) and runs the markers `integration` and `tools`. Expected end (Phase 2): `… passed, 2 skipped`. The first run builds the test image (about 2 minutes) and downloads the Python packages into a Docker volume |
| `make docs-check` | Checks that the English and Polish documentation are consistent |
| `make messages` | Extracts UI strings into `messages.pot` and updates the Polish catalog `messages.po`; then fill in the new `msgstr` entries |
| `make up` / `make down` / `make logs` / `make build` | Start, stop, follow logs and build the Docker stack (needs Docker) |
| `make migrate` | Applies database migrations in the dev stack (`baza-wiedzy migrate` in a one-off `app` container). Expected: `Database schema is up to date (heads: core_0003, documents_0002, ingestion_0001, search_0001).` |
| `make revision branch=<branch> m="text"` | Creates an empty migration file in the branch of the core or a module (`core`, `search`, ...), on the host, no database needed |
| `make shell` | Shell inside the `app` container (needs Docker) |
| `make probki DIR=<directory>` | Writes synthetic sample files for a manual test of every format and error case (TXT cp1250 and cp852, DOCX, DOC, text PDF, scanned PDF, encrypted PDF, EML and MSG with attachments, damaged files) into the directory, with `OCZEKIWANE.txt` listing the expected result of each; runs in the test image (LibreOffice for the DOC) |
| `make seed-samples` | Reserved for loading synthetic sample documents (not available yet) |

If `make` is not available, run the same tools through `uv run`, for example `uv run pytest`.

Known warnings (harmless, tracked for the monthly dependency review):
- `pytest`: `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.` Starlette 1.10 announces a change of the HTTP client used by FastAPI's `TestClient`. Tests are not affected; revisit when FastAPI/Starlette document the replacement.
- `pytest`: `DeprecationWarning: tagMap is deprecated` / `typeMap is deprecated` from `ldap3/utils/asn1.py`: ldap3 2.9.1 uses old names of `pyasn1`. Harmless; revisit when ldap3 publishes a fix.
- `make up`: Caddy logs `admin endpoint disabled`, `HTTP/2 skipped because it requires TLS` (for the port-80 redirect server) and `stapling OCSP` (internal certificate). All expected.

Dependencies: versions are pinned in `uv.lock` and only releases older than `exclude-newer` (in `pyproject.toml`) are resolved. Upgrades happen in the monthly dependency review or, for security fixes, as an exception; the procedure is in ADR-015 ([decisions.md](decisions.md)). Dependabot (`.github/dependabot.yml`) opens monthly pull requests for GitHub Actions SHAs, Dockerfile base images and the images in `docker-compose.yml` (only releases older than 14 days), and security pull requests for Python packages. Base images only get patch releases within PostgreSQL 16.x and Python 3.12.x: moving to a new PostgreSQL major version (for example 18) or Python minor version (for example 3.13) is a planned migration with its own ADR and procedure (database dump and restore, a new dictionary spike with the golden search tests, `reindex-search`, dependency review), never a routine update. Dependabot's two-week cooldown does not work for Docker images (the registry gives no publication dates): in the monthly review check each Docker PR by hand (release notes of the new tag, published at least two weeks ago, CI green) before merging.

Security alert for a Python package that Dependabot cannot fix because of `exclude-newer`: (1) assess the exposure (is the vulnerable code reachable in this application? write the conclusion down, ideally as a test); (2) unblock only that package: add it to `exclude-newer-package` in `[tool.uv]` of `pyproject.toml` with a date just after the fixed release, then `uv lock --upgrade-package <name>`; (3) check `git diff uv.lock`: only that package (and, if unavoidable, its direct dependencies) may change; (4) entry under `### Security` in `[Unreleased]` of both changelogs and a line in ADR-015; (5) `make lint test test-db docs-check smoke`; (6) remove the exception at the next monthly review. Example: Dependabot alert #1 (`opentelemetry-api` 1.45.2) in ADR-015.

Local stack: `cp .env.example .env` once and set `SECRET_KEY` to a generated value (the application refuses the placeholder); the other defaults are fine for development. Then `make build` and `make up`. `make up` builds the images locally (`docker-compose.dev.yml`), mounts `src/` into `app` and `worker` and reloads the web server on change. Open `https://localhost`: the certificate comes from Caddy's internal CA, so the **browser shows a certificate warning**; accept it for `localhost` (or import the root certificate: `docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt .`). From the command line use `curl -k https://localhost/`. `http://localhost` answers `301` to `https://localhost/`. On a machine with little memory (for example WSL2, which by default gets half of the host RAM) lower the limits in your development `.env`: `POSTGRES_MAX_CONNECTIONS=20`, `DB_MEMORY_LIMIT=1g`, `WORKER_MEMORY_LIMIT=1536m`, `APP_MEMORY_LIMIT=512m`; check the memory available to Docker with `docker info --format '{{.MemTotal}}'`. The defaults are sized for the server. After the first `make up` run `make migrate`; then `curl -k https://localhost/ready` returns `"status":"ok"` with all four checks `ok`. Create a local admin with `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec app baza-wiedzy create-admin bw-admin` and log in at `https://localhost/login`. In development `app` and `worker` give up after 3 failed starts (`restart: on-failure:3`) instead of restarting forever; `make logs` shows why. The web server reloads on code changes; the worker does not: after changing task code run `docker compose -f docker-compose.yml -f docker-compose.dev.yml restart worker`.

## Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| Uploaded files stay "oczekuje" (`pending`) | Worker or Redis down, or the job was lost while Redis was down | `docker compose ps`, `docker compose logs worker redis`; once both run, a worker restart (`docker compose restart worker`) or `docker compose exec app baza-wiedzy reprocess --stuck` enqueues waiting versions again |
| A document hangs in "przetwarzanie" | The worker was killed during an attempt (memory limit, restart) | Wait `INGEST_STALE_MINUTES` and restart the worker, or `docker compose exec app baza-wiedzy reprocess --stuck`; the worker log has the version ID (`docker compose logs worker \| grep <version id>`) |
| "błąd": "Nie rozpoznano kodowania tekstu…" | TXT in a code page other than UTF-8, Windows-1250, ISO-8859-2 (for example DOS CP852) | Save the file as UTF-8 and upload it again (ADR-022) |
| "błąd": "Chwilowy problem…" repeated, then "Przetwarzanie kilka razy nie powiodło się…" | Database or Redis unavailable for longer than the retries, or attempts above `INGEST_TIME_LIMIT_SECONDS` | Fix the cause (`docker compose logs worker`: exception class per version), then "Przetwórz ponownie" or `baza-wiedzy reprocess --failed` |
| "błąd": "Przetwarzanie trwało dłużej niż limit czasu." on a DOC or PDF | LibreOffice or the PDF child process ran out of `LIBREOFFICE_TIMEOUT_SECONDS` / `INGEST_CHILD_TIMEOUT_SECONDS` in every attempt (very large or broken file, overloaded server) | `docker compose logs worker \| grep <version id>` (`IsolationTimeout`); raise the limit in `.env` if the file is legitimate, `docker compose up -d worker`, then "Przetwórz ponownie" |
| "błąd": "…zabezpieczony hasłem…" | Password-protected PDF, DOC or DOCX | Upload a copy without a password; the application never asks for passwords |
| `baza-wiedzy events redeliver` reports failed deliveries | An event handler raises (exception class in the log) | Fix the handler (a bug: report it), run the command again; entries stay until delivered |
| `failed` with timeout on a scan | Very large or poor-quality scan | Increase `OCR_TIMEOUT_SECONDS`, rescan, reprocess |
| AD users see "Logowanie kontem firmowym jest chwilowo niedostępne" | Domain controllers unreachable, certificate problem or service account refused (audit `auth.ad_unavailable`, `reason`) | `docker compose logs app \| grep 'Active Directory'`; `docker compose exec app baza-wiedzy ad-check`; for `tls`: `openssl s_client -connect <dc>:636 -CAfile secrets/ldap/ca.pem` and the host name in `LDAP_URI`. The break-glass admin still works |
| `app` exits with `Active Directory configuration: ...` | `LDAP_ENABLED=true` with an incomplete configuration (missing CA file, `ldap://` URI, empty bind DN) | Fix the variable named in the message, or set `LDAP_ENABLED=false` |
| One AD user cannot log in, others can | Wrong password, account disabled or expired in AD, name taken by a local account, name outside `LDAP_BASE_DN` | `docker compose exec app baza-wiedzy ad-check --user <name> --password`; audit `auth.login_failed` `reason`; `docker compose logs app` for `AD login refused` |
| Inflected words are not found | Dictionary not loaded or index not rebuilt | `SELECT ts_lexize('pl_hunspell','faktury');`, run `reindex-search` |
| `/ready` fails after update | Migration not applied | `docker compose run --rm app baza-wiedzy migrate`, then see `update.log` |
| Disk full | Backups or derived files | Check `backups/`, retention settings |
| `could not open dictionary file ".../pl_pl.dict"` | The database runs a plain `postgres` image instead of the project's `db` image | `docker compose images db`; rebuild or pull the `db` image |
| `app` exits with `SECRET_KEY is missing or too weak` | `SECRET_KEY` shorter than 32 characters or a placeholder | Generate a key (see Configuration), put it in `.env`, `docker compose up -d app` |
| "Zbyt wiele nieudanych prób" (HTTP 429) on login | Failed-login lock for the name and/or address | Wait `LOGIN_LOCKOUT_MINUTES`, or `docker compose exec app baza-wiedzy unlock-login --user <name>` / `--ip <address>` |
| `docker compose up`: `Pool overlaps with other one on this address space` | `DOCKER_SUBNET` collides with another Docker network or route | Choose another `DOCKER_SUBNET` and `PROXY_IP` (Configuration) |
| 403 page "Żądanie odrzucone" | Page open for a long time across a `SECRET_KEY` change or a new session, or a blocked cross-site request | Reload the page and repeat the action; the reason is in the log: `docker compose logs app \| grep 'CSRF check refused'` (`missing_token` on every submit of one form = a template bug, report it) |
| Upload ends with "Plik jest większy niż dozwolony rozmiar" (413), or the browser shows a proxy error for big files | File above `MAX_UPLOAD_MB`, or `MAX_REQUEST_MB` (proxy) not above `MAX_UPLOAD_MB` | Raise both in `.env` (`MAX_REQUEST_MB` = `MAX_UPLOAD_MB` + 1), `docker compose up -d proxy app` |
| Files accumulate in `/data/storage/.incoming` | Uploads interrupted (browser closed, app restarted) | Harmless; removed after an hour at the next upload, or now: `docker compose exec app baza-wiedzy storage-cleanup` (expected: `Removed N orphaned incoming file(s) ...`) |
| Application refuses to start: `module ... requires ...` | `ENABLED_MODULES` lists a module without one it depends on | Add the required module to `ENABLED_MODULES` in `.env`, or remove the dependent one |

## Security notes for operators

- Only ports 80/443 are published. Database and Redis are not reachable from the network; Redis also requires a password (`REDIS_PASSWORD`).
- `worker`, `db` and `redis` are only on the internal Docker network `backend` (no route to the internet, no name resolution of public names); `app` is on it and on `default` (reached by the proxy, outgoing LDAPS to Active Directory). Healthchecks run inside the containers and are not affected; `scripts/offline_bundle.sh` lists images only and needs no change; the coming `update.sh` (Phase 6) runs `docker compose`, which creates or updates both networks. Check: `make smoke` ("Network isolation"), or on a server `docker compose exec worker python -c "import socket; socket.create_connection(('1.1.1.1', 443), 3)"` must fail with `OSError` (network unreachable).
- Containers run as non-root users: `app` and `worker` as uid 10001, `proxy` as uid 10002 (listening on 8080/8443 inside, no capabilities), `db` and `redis` drop to their own users after start. `app`, `worker` and `proxy` drop all Linux capabilities; all services set `no-new-privileges`.
- Docker logs rotate: at most 5 files × 10 MB per container.
- `.env` and backups contain secrets and personal data; restrict permissions.
- Update the base images regularly (a new release rebuilds them).
- Do not give the application access to `docker.sock`; updates are done from the host.
