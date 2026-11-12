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
├── docker/caddy/        # Dockerfile and Caddyfile of the proxy (built on the server)
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
| `ENABLED_MODULES` | Optional modules to run, comma-separated (default `documents,ingestion,search`); the core is always on |
| `PUBLIC_HOSTNAME` | DNS name used by Caddy |
| `TLS_MODE` | `internal`, `files` or `acme` |
| `SECRET_KEY` | Signing key for sessions/CSRF |
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

`ENABLED_MODULES` must have the same value for `app` and `worker` (both read the same `.env`). At startup the application checks module dependencies and refuses to start with a message such as `module 'search' requires 'documents', which is not enabled`. `baza-wiedzy migrate` applies the migration branches of the enabled modules only; after enabling a module later, run `migrate` again. Disabling a module does not delete its tables or data.

Database user and privileges: the application and `baza-wiedzy migrate` connect as one database user that is **not** a superuser but **owns the application database**. That is enough for everything the migrations do: `CREATE EXTENSION pg_trgm` and `CREATE EXTENSION unaccent` (both are "trusted" extensions in PostgreSQL 13+, so the database owner may create them; the extension files are already in the `db` image), and `CREATE TEXT SEARCH DICTIONARY` / `CREATE TEXT SEARCH CONFIGURATION` (need `CREATE` on schema `public`, which the database owner has in PostgreSQL 15+). If the database is created by someone else, grant: `GRANT CREATE ON DATABASE <db> TO <user>; GRANT CREATE ON SCHEMA public TO <user>;`. `make test-db` runs the search objects as such a non-superuser, so a regression shows up in tests. On the first start of an empty `pgdata` volume, the `db` image creates this user and database from `APP_DB_USER`, `APP_DB_PASSWORD` and `APP_DB_NAME` (`docker/db/initdb/10-app-role.sh`). Changing these variables later does **not** change an existing database; change the password with `ALTER ROLE` as `postgres` and update `.env` at the same time.

### Database connections and memory

Every PostgreSQL connection loads the Polish dictionary on first use and keeps it: about 27–30 MB per connection (measured in the spike, see [search.md](search.md)). The connection limit is therefore set explicitly and the `db` memory limit is derived from it:

- Connection budget: app 5 + 5 overflow per app process, worker 1 per worker process, plus a few for migrations, backups and manual `psql`. With 2 app processes and 2 worker processes: 2 × 10 + 2 × 1 + 6 = 28, so the default `POSTGRES_MAX_CONNECTIONS=40` leaves room.
- Memory: `DB_MEMORY_LIMIT` ≥ `POSTGRES_SHARED_BUFFERS` + `POSTGRES_MAX_CONNECTIONS` × 35 MB + 300 MB. Defaults: 256 MB + 40 × 35 MB + 300 MB ≈ 1.95 GB → `DB_MEMORY_LIMIT=2g`.
- If you raise `POSTGRES_MAX_CONNECTIONS`, raise `DB_MEMORY_LIMIT` by 35 MB per extra connection. Otherwise the kernel may kill PostgreSQL (`docker compose ps` shows `db` restarting; `docker inspect baza-wiedzy-db-1 --format '{{.State.OOMKilled}}'` prints `true`).
- Check the current values: `docker compose exec db psql -U postgres -c 'SHOW max_connections' -c 'SELECT count(*) FROM pg_stat_activity'`.

## First installation

1. Copy `docker-compose.yml`, `.env.example`, `docker/caddy/` and `scripts/` to `/srv/baza-wiedzy/`; rename `.env.example` to `.env` and fill it in; `chmod 600 .env`.
2. Log in to the registry: `docker login ghcr.io` with a token that can read packages.
3. Set `APP_VERSION` and `IMAGE_PREFIX`, then start: `docker compose pull --ignore-buildable && docker compose build proxy && docker compose up -d`. The proxy image is built on the server from `docker/caddy/` (a few seconds; it needs the `caddy` base image from Docker Hub).
4. Run migrations: `docker compose run --rm app baza-wiedzy migrate`.
5. Create the first admin: `docker compose exec app baza-wiedzy create-admin <username>`.
6. Check `https://<PUBLIC_HOSTNAME>/ready` returns OK.
7. Log in as the admin, configure AD group mapping, test an AD login.
8. Schedule backups (see below).

## Releases

- Versioning: SemVer. A Git tag `vX.Y.Z` on `main` triggers the release workflow.
- The workflow builds and pushes three images: `baza-wiedzy-app`, `baza-wiedzy-worker`, `baza-wiedzy-db`, tagged `X.Y.Z` (and `latest` only for convenience in tests, never used in production).
- Before tagging: move `[Unreleased]` in both changelogs under the new version number and date.

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

On a machine with internet access: `docker pull` the three images, `docker save` to a file, copy it to the server, `docker load`, then run `update.sh` with `SKIP_PULL=1`.

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

Logs are JSON on stdout. Compose sets Docker log rotation (`max-size 10m`, `max-file 5`). Read them with `docker compose logs -f app worker`.

## Developer commands

Everything goes through the `Makefile` (run from Git Bash on Windows or from a Linux shell). Python 3.12 and the virtual environment are managed by `uv`; run `uv sync` once.

| Command | What it does |
|---|---|
| `make help` | Lists all commands |
| `make lint` | `ruff`, `mypy` and the module boundary check (`lint-imports`) |
| `make fmt` | Formats and auto-fixes with `ruff` |
| `make test` | Runs the tests with `pytest` (integration and golden search tests are skipped) |
| `make test-db` | Runs the integration and golden search tests in Docker: a throwaway PostgreSQL from `docker/db` on tmpfs and a one-off test runner on a private Compose network (`docker-compose.test.yml`); no port is published. Expected end: `17 passed, 2 skipped` (Phase 0). The first run downloads the Python packages into a Docker volume |
| `make docs-check` | Checks that the English and Polish documentation are consistent |
| `make messages` | Extracts UI strings into `messages.pot` and updates the Polish catalog `messages.po`; then fill in the new `msgstr` entries |
| `make up` / `make down` / `make logs` / `make build` | Start, stop, follow logs and build the Docker stack (needs Docker) |
| `make migrate` | Applies database migrations in the dev stack (`baza-wiedzy migrate` in a one-off `app` container). Expected: `Database schema is up to date (core@head, search@head).` |
| `make revision branch=<branch> m="text"` | Creates an empty migration file in the branch of the core or a module (`core`, `search`, ...), on the host, no database needed |
| `make shell` | Shell inside the `app` container (needs Docker) |
| `make seed-samples` | Reserved for loading synthetic sample documents (not available yet) |

If `make` is not available, run the same tools through `uv run`, for example `uv run pytest`.

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
