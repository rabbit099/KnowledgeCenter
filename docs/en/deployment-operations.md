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
├── data/                # volumes: storage/, postgres/, caddy/
└── backups/             # created by backup.sh
```

## Configuration

All settings are environment variables in `.env`; `.env.example` in the repository documents every variable and is the source of truth. Key variables:

| Variable | Meaning |
|---|---|
| `APP_NAME` | Display name (default "Baza Wiedzy") |
| `APP_VERSION` | Image tag to run, for example `1.0.0` |
| `PUBLIC_HOSTNAME` | DNS name used by Caddy |
| `TLS_MODE` | `internal`, `files` or `acme` |
| `SECRET_KEY` | Signing key for sessions/CSRF |
| `DATABASE_URL` / `POSTGRES_PASSWORD` | Database access |
| `REDIS_URL` | Queue |
| `MAX_UPLOAD_MB` and other limits | See [ingestion-and-ocr.md](ingestion-and-ocr.md) |
| `LDAP_*` | See [auth-and-permissions.md](auth-and-permissions.md) |

## First installation

1. Copy `docker-compose.yml`, `.env.example` and `scripts/` to `/srv/baza-wiedzy/`; rename `.env.example` to `.env` and fill it in; `chmod 600 .env`.
2. Log in to the registry: `docker login ghcr.io` with a token that can read packages.
3. Set `APP_VERSION` and start: `docker compose pull && docker compose up -d`.
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
9. Appends the result to `data/update.log`.

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

## Backup

`scripts/backup.sh` creates `/srv/baza-wiedzy/backups/YYYY-MM-DD_HHMMSS/` containing:
- `db.dump` — `pg_dump -Fc`
- `storage.tar` — original and derived files
- `env.backup` — copy of `.env` (contains secrets; protect the backup location)
- `SHA256SUMS`

Default retention: 7 daily, 4 weekly, 6 monthly (`BACKUP_KEEP_*`). Copying backups off the server (NAS, other server, object storage) is a hook: set `BACKUP_REMOTE_CMD` to a command that receives the backup directory path. The destination is decided later; until then backups live only on the same server and that is **not** a sufficient backup.

Schedule (cron, daily at night):

```
15 2 * * * /srv/baza-wiedzy/scripts/backup.sh >> /srv/baza-wiedzy/data/backup.log 2>&1
```

Redis holds only the job queue; it needs no backup (unfinished jobs are re-queued with `baza-wiedzy reprocess --pending`).

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
| `/ready` | Database reachable, Redis reachable, worker heartbeat recent, schema version equals app version |

Zabbix: an HTTP check on `/ready` (expect 200) plus a trigger on the response time. Metrics endpoint and queue length are optional later additions.

Logs are JSON on stdout. Compose sets Docker log rotation (`max-size 10m`, `max-file 5`). Read them with `docker compose logs -f app worker`.

## Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| Uploaded files stay `pending` | Worker or Redis down | `docker compose ps`, `docker compose logs worker redis` |
| `failed` with timeout on a scan | Very large or poor-quality scan | Increase `OCR_TIMEOUT_SECONDS`, rescan, reprocess |
| AD login fails for everyone | LDAPS certificate, bind account, DNS | `docker compose logs app`, check `LDAP_CA_FILE`, test with `ldapsearch` |
| Inflected words are not found | Dictionary not loaded or index not rebuilt | `SELECT ts_lexize('pl_hunspell','faktury');`, run `reindex-search` |
| `/ready` fails after update | Migration not applied | `docker compose run --rm app baza-wiedzy migrate`, then see `update.log` |
| Disk full | Backups or derived files | Check `backups/`, retention settings |

## Security notes for operators

- Only ports 80/443 are published. Database and Redis are not reachable from the network.
- `.env` and backups contain secrets and personal data; restrict permissions.
- Update the base images regularly (a new release rebuilds them).
- Do not give the application access to `docker.sock`; updates are done from the host.
