# Deployment and operations

> Applies to: v0.0.0 (pre-release) · Last reviewed: 2026-11-12

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
├── logs/                # update.log, backup.log
└── backups/             # created by backup.sh
```

Data lives in Docker named volumes of the Compose project `baza-wiedzy`: `pgdata` (database), `storage` (uploaded and derived files, mounted at `/data/storage` in `app` and `worker`), `redis-data` (queue), `caddy-data` and `caddy-config` (certificates, including Caddy's internal CA), `clamav-db` (only with the `antivirus` profile). List them with `docker volume ls --filter name=baza-wiedzy`. `docker compose down` keeps them; `docker compose down -v` **deletes all data**.

## Configuration

All settings are environment variables in `.env`; `.env.example` in the repository documents every variable and is the source of truth. Key variables:

| Variable | Meaning |
|---|---|
| `APP_NAME` | Display name (default "Baza Wiedzy") |
| `APP_VERSION` | Image tag to run, for example `1.0.0` |
| `LOG_LEVEL` | `DEBUG`, `INFO` (default), `WARNING`, `ERROR`; logs are JSON lines (see Monitoring) |
| `ENABLED_MODULES` | Optional modules to run, comma-separated (default `documents,ingestion,search`); the core is always on |
| `PUBLIC_HOSTNAME` | DNS name used by Caddy |
| `TLS_MODE` | `internal`, `files` or `acme` |
| `SECRET_KEY` | Signing key for sessions/CSRF |
| `HTTP_PORT`, `HTTPS_PORT` | Host ports of the proxy (default 80, 443) |
| `IMAGE_PREFIX` | Registry and name prefix of the images, for example `ghcr.io/<owner>/baza-wiedzy` |
| `ACME_EMAIL`, `MAX_REQUEST_MB` | Let's Encrypt contact (`TLS_MODE=acme`); largest request body accepted by the proxy |
| `POSTGRES_PASSWORD` | Password of the PostgreSQL superuser; used only inside the `db` container |
| `APP_DB_USER` / `APP_DB_PASSWORD` / `APP_DB_NAME` | Non-superuser that owns the application database; the application and migrations use it. `DATABASE_URL` is built from these in `docker-compose.yml` |
| `POSTGRES_MAX_CONNECTIONS`, `POSTGRES_SHARED_BUFFERS` | Explicit connection limit and shared buffers (see below) |
| `DB_MEMORY_LIMIT`, `APP_MEMORY_LIMIT`, `WORKER_MEMORY_LIMIT`, `REDIS_MEMORY_LIMIT`, `CLAMAV_MEMORY_LIMIT` | Container memory limits |
| `WORKER_PROCESSES` | Worker processes (default 2; one thread and one database connection each; not more than CPU cores) |
| `REDIS_PASSWORD` | Password required by Redis (`requirepass`); URL-safe characters only. `REDIS_URL` is built from it in `docker-compose.yml` |
| `MAX_UPLOAD_MB` and other limits | See [ingestion-and-ocr.md](ingestion-and-ocr.md) |
| `LDAP_*` | See [auth-and-permissions.md](auth-and-permissions.md) |

`ENABLED_MODULES` must have the same value for `app` and `worker` (both read the same `.env`). At startup the application checks module dependencies and refuses to start with a message such as `module 'search' requires 'documents', which is not enabled`. `baza-wiedzy migrate` applies the migrations of the core and of **every** module, independent of `ENABLED_MODULES`: the schema is always complete, so enabling a module later needs no migration step and `/ready` compares the database with all migration heads. Disabling a module does not delete its tables or data.

Database user and privileges: the application and `baza-wiedzy migrate` connect as one database user that is **not** a superuser but **owns the application database**. That is enough for everything the migrations do: `CREATE EXTENSION pg_trgm` and `CREATE EXTENSION unaccent` (both are "trusted" extensions in PostgreSQL 13+, so the database owner may create them; the extension files are already in the `db` image), and `CREATE TEXT SEARCH DICTIONARY` / `CREATE TEXT SEARCH CONFIGURATION` (need `CREATE` on schema `public`, which the database owner has in PostgreSQL 15+). If the database is created by someone else, grant: `GRANT CREATE ON DATABASE <db> TO <user>; GRANT CREATE ON SCHEMA public TO <user>;`. `make test-db` runs the search objects as such a non-superuser, so a regression shows up in tests. On the first start of an empty `pgdata` volume, the `db` image creates this user and database from `APP_DB_USER`, `APP_DB_PASSWORD` and `APP_DB_NAME` (`docker/db/initdb/10-app-role.sh`). Changing these variables later does **not** change an existing database; change the password with `ALTER ROLE` as `postgres` and update `.env` at the same time.

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
4. Run migrations: `docker compose run --rm app baza-wiedzy migrate`.
5. Create the first admin: `docker compose exec app baza-wiedzy create-admin <username>`.
6. Check `https://<PUBLIC_HOSTNAME>/ready` returns OK.
7. Log in as the admin, configure AD group mapping, test an AD login.
8. Schedule backups (see below).

## Releases

- Versioning: SemVer. A Git tag `vX.Y.Z` on `main` triggers `.github/workflows/release.yml`.
- Before tagging: set `version = "X.Y.Z"` in `pyproject.toml` (the application reads its version from there), move `[Unreleased]` in both changelogs under `## [X.Y.Z] - YYYY-MM-DD`, add a new empty `[Unreleased]`, commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`. Check locally first: `uv run python scripts/check_release.py vX.Y.Z` must print `Release vX.Y.Z matches pyproject.toml and both changelogs.`
- The workflow (1) refuses the release if the tag differs from `pyproject.toml` or a changelog has no `## [X.Y.Z]` section, (2) runs the complete CI (including the smoke test), (3) builds and pushes four images for `linux/amd64`: `ghcr.io/<owner>/baza-wiedzy-app`, `-worker`, `-db`, `-proxy`, all tagged `X.Y.Z` (no `latest`). Each image carries the labels `org.opencontainers.image.version`, `.revision` (the commit SHA), `.source` and `.created`. The run summary lists every image with its digest; record them in the release notes.
- Check what a server runs: `docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' <image>`.
- Workflow security: actions are pinned to commit SHAs; the default token is read-only (`contents: read`), only the publish job of `release.yml` gets `packages: write`; CI uses no secrets, so pull requests from forks have nothing to leak.

CI (`.github/workflows/ci.yml`, on pushes to `main` and on pull requests) runs three jobs: `quality` (`make lint`: ruff, mypy, import-linter; `make test`; `make docs-check`), `integration` (`make test-db`: PostgreSQL from the project image and Redis) and `images` (`make smoke`: builds the four images for `linux/amd64`, starts `docker-compose.yml`, migrates and requires `/ready` to report 4 × `ok`). Run the same locally before pushing.

## Updating

```
cd /srv/baza-wiedzy
./scripts/update.sh 1.2.0
```

What `update.sh` does:
1. Pre-checks: Docker running, free disk space, the new images exist in the registry, `.env` contains all variables listed in the new `.env.example` (otherwise it stops and lists the missing ones).
2. Runs `backup.sh` (database + files).
3. Pulls the new images.
4. Stops `worker` so no jobs run during the migration.
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

Everything goes through the `Makefile` (run from Git Bash on Windows or from a Linux shell). Python 3.12 and the virtual environment are managed by `uv`; run `uv sync` once.

| Command | What it does |
|---|---|
| `make help` | Lists all commands |
| `make lint` | `ruff`, `mypy` and the module boundary check (`lint-imports`) |
| `make fmt` | Formats and auto-fixes with `ruff` |
| `make test` | Runs the tests with `pytest` (integration and golden search tests are skipped) |
| `make smoke` | Smoke test of the production Compose file: builds the four images (`linux/amd64`), starts `docker-compose.yml` as project `baza-wiedzy-smoke` on ports 18080/18443 with generated secrets, migrates, requires `/ready` = 4 × `ok`, removes everything. Expected end: `==> Smoke test passed: /ready reports 4 x ok` (about 1 minute with cached layers) |
| `make test-db` | Runs the integration and golden search tests in Docker: a throwaway PostgreSQL from `docker/db` on tmpfs and a one-off test runner on a private Compose network (`docker-compose.test.yml`); no port is published. Expected end: `17 passed, 2 skipped` (Phase 0). The first run downloads the Python packages into a Docker volume |
| `make docs-check` | Checks that the English and Polish documentation are consistent |
| `make messages` | Extracts UI strings into `messages.pot` and updates the Polish catalog `messages.po`; then fill in the new `msgstr` entries |
| `make up` / `make down` / `make logs` / `make build` | Start, stop, follow logs and build the Docker stack (needs Docker) |
| `make migrate` | Applies database migrations in the dev stack (`baza-wiedzy migrate` in a one-off `app` container). Expected: `Database schema is up to date (heads: core_0001, search_0001).` |
| `make revision branch=<branch> m="text"` | Creates an empty migration file in the branch of the core or a module (`core`, `search`, ...), on the host, no database needed |
| `make shell` | Shell inside the `app` container (needs Docker) |
| `make seed-samples` | Reserved for loading synthetic sample documents (not available yet) |

If `make` is not available, run the same tools through `uv run`, for example `uv run pytest`.

Known warnings (harmless, tracked for the monthly dependency review):
- `pytest`: `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.` Starlette 1.10 announces a change of the HTTP client used by FastAPI's `TestClient`. Tests are not affected; revisit when FastAPI/Starlette document the replacement.
- `make up`: Caddy logs `admin endpoint disabled`, `HTTP/2 skipped because it requires TLS` (for the port-80 redirect server) and `stapling OCSP` (internal certificate). All expected.

Dependencies: versions are pinned in `uv.lock` and only releases older than `exclude-newer` (in `pyproject.toml`) are resolved. Upgrades happen in the monthly dependency review or, for security fixes, as an exception; the procedure is in ADR-015 ([decisions.md](decisions.md)). Dependabot (`.github/dependabot.yml`) opens monthly pull requests for GitHub Actions SHAs, Dockerfile base images and the images in `docker-compose.yml` (only releases older than 14 days), and security pull requests for Python packages.

Local stack: `cp .env.example .env` once (the defaults are fine for development), then `make build` and `make up`. `make up` builds the images locally (`docker-compose.dev.yml`), mounts `src/` into `app` and `worker` and reloads the web server on change. Open `https://localhost`: the certificate comes from Caddy's internal CA, so the **browser shows a certificate warning**; accept it for `localhost` (or import the root certificate: `docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt .`). From the command line use `curl -k https://localhost/`. `http://localhost` answers `301` to `https://localhost/`. On a machine with little memory (for example WSL2, which by default gets half of the host RAM) lower the limits in your development `.env`: `POSTGRES_MAX_CONNECTIONS=20`, `DB_MEMORY_LIMIT=1g`, `WORKER_MEMORY_LIMIT=1536m`, `APP_MEMORY_LIMIT=512m`; check the memory available to Docker with `docker info --format '{{.MemTotal}}'`. The defaults are sized for the server. After the first `make up` run `make migrate`; then `curl -k https://localhost/ready` returns `"status":"ok"` with all four checks `ok`. In development `app` and `worker` give up after 3 failed starts (`restart: on-failure:3`) instead of restarting forever; `make logs` shows why. The web server reloads on code changes; the worker does not: after changing task code run `docker compose -f docker-compose.yml -f docker-compose.dev.yml restart worker`.

## Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| Uploaded files stay `pending` | Worker or Redis down | `docker compose ps`, `docker compose logs worker redis` |
| `failed` with timeout on a scan | Very large or poor-quality scan | Increase `OCR_TIMEOUT_SECONDS`, rescan, reprocess |
| AD login fails for everyone | LDAPS certificate, bind account, DNS | `docker compose logs app`, check `LDAP_CA_FILE`, test with `ldapsearch` |
| Inflected words are not found | Dictionary not loaded or index not rebuilt | `SELECT ts_lexize('pl_hunspell','faktury');`, run `reindex-search` |
| `/ready` fails after update | Migration not applied | `docker compose run --rm app baza-wiedzy migrate`, then see `update.log` |
| Disk full | Backups or derived files | Check `backups/`, retention settings |
| `could not open dictionary file ".../pl_pl.dict"` | The database runs a plain `postgres` image instead of the project's `db` image | `docker compose images db`; rebuild or pull the `db` image |
| Application refuses to start: `module ... requires ...` | `ENABLED_MODULES` lists a module without one it depends on | Add the required module to `ENABLED_MODULES` in `.env`, or remove the dependent one |

## Security notes for operators

- Only ports 80/443 are published. Database and Redis are not reachable from the network; Redis also requires a password (`REDIS_PASSWORD`).
- Containers run as non-root users: `app` and `worker` as uid 10001, `proxy` as uid 10002 (listening on 8080/8443 inside, no capabilities), `db` and `redis` drop to their own users after start. `app`, `worker` and `proxy` drop all Linux capabilities; all services set `no-new-privileges`.
- Docker logs rotate: at most 5 files × 10 MB per container.
- `.env` and backups contain secrets and personal data; restrict permissions.
- Update the base images regularly (a new release rebuilds them).
- Do not give the application access to `docker.sock`; updates are done from the host.
