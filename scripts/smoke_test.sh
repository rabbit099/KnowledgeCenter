#!/usr/bin/env bash
# Smoke test of the PRODUCTION Compose file with freshly built images (make smoke, CI).
#   1. build app, worker, db, proxy for linux/amd64 and tag them as <prefix>-<name>:<version>
#   2. start docker-compose.yml in a separate Compose project with generated secrets
#   3. run `baza-wiedzy migrate`
#   4. require /ready to answer HTTP 200 with database, redis, schema and worker all "ok"
#   5. create the break-glass admin (generated password, --password-stdin) and, through the
#      proxy: refuse a wrong password, log in, open the admin panel, log out
# Everything (containers, volumes, the temporary env file) is removed at the end.
# Ports: SMOKE_HTTP_PORT / SMOKE_HTTPS_PORT (default 18080 / 18443), so a running dev stack
# on 80/443 is not disturbed.
set -euo pipefail

cd "$(dirname "$0")/.."
PREFIX="${SMOKE_IMAGE_PREFIX:-local/baza-wiedzy}"
VERSION="${SMOKE_VERSION:-smoke}"
HTTPS_PORT="${SMOKE_HTTPS_PORT:-18443}"
HTTP_PORT="${SMOKE_HTTP_PORT:-18080}"
PROJECT="baza-wiedzy-smoke"
SKIP_BUILD="${SMOKE_SKIP_BUILD:-0}"

secret() { python3 -c 'import secrets; print(secrets.token_urlsafe(24))'; }

if [ "$SKIP_BUILD" != "1" ]; then
    echo "==> Building images ($PREFIX-*:$VERSION, linux/amd64)"
    docker build --platform linux/amd64 -q -t "$PREFIX-app:$VERSION" -f docker/app/Dockerfile .
    docker build --platform linux/amd64 -q -t "$PREFIX-worker:$VERSION" -f docker/worker/Dockerfile .
    docker build --platform linux/amd64 -q -t "$PREFIX-db:$VERSION" docker/db
    docker build --platform linux/amd64 -q -t "$PREFIX-proxy:$VERSION" docker/caddy
fi

ENV_FILE="$(mktemp)"
cat > "$ENV_FILE" <<ENV
APP_NAME=Baza Wiedzy (smoke)
APP_VERSION=$VERSION
IMAGE_PREFIX=$PREFIX
PUBLIC_HOSTNAME=localhost
TLS_MODE=internal
HTTP_PORT=$HTTP_PORT
HTTPS_PORT=$HTTPS_PORT
DOCKER_SUBNET=${SMOKE_SUBNET:-172.30.71.0/24}
PROXY_IP=${SMOKE_PROXY_IP:-172.30.71.10}
SECRET_KEY=$(secret)
POSTGRES_PASSWORD=$(secret)
APP_DB_PASSWORD=$(secret)
REDIS_PASSWORD=$(secret)
DB_MEMORY_LIMIT=1g
POSTGRES_MAX_CONNECTIONS=20
ENV

compose() { docker compose -p "$PROJECT" -f docker-compose.yml --env-file "$ENV_FILE" "$@"; }

# shellcheck disable=SC2317  # invoked through trap
cleanup() {
    status=$?
    if [ "$status" -ne 0 ]; then
        echo "==> FAILED; state and logs:"
        compose ps -a || true
        compose logs --no-color --tail 50 || true
    fi
    compose down -v --remove-orphans >/dev/null 2>&1 || true
    rm -f "$ENV_FILE"
    exit "$status"
}
trap cleanup EXIT

echo "==> Starting the production stack (project $PROJECT)"
compose up -d --wait --wait-timeout 120 db redis
compose up -d
echo "==> Migrating"
compose run --rm app baza-wiedzy migrate

echo "==> Waiting for /ready (https://localhost:$HTTPS_PORT/ready)"
body=""
for _ in $(seq 60); do
    body="$(curl -sk --max-time 5 "https://localhost:$HTTPS_PORT/ready" || true)"
    if python3 - "$body" <<'PY'
import json, sys
try:
    data = json.loads(sys.argv[1])
except ValueError:
    sys.exit(1)
expected = {"database": "ok", "redis": "ok", "schema": "ok", "worker": "ok"}
sys.exit(0 if data.get("status") == "ok" and data.get("checks") == expected else 1)
PY
    then
        echo "$body"
        echo "==> /ready reports 4 x ok"
        ready=1
        break
    fi
    sleep 2
done
if [ "${ready:-0}" != "1" ]; then
    echo "Last /ready answer: ${body:-<none>}"
    exit 1
fi

echo "==> Login through the proxy"
BASE="https://localhost:$HTTPS_PORT"
ADMIN_PASSWORD="$(secret)"
JAR="$(mktemp)"
printf '%s\n' "$ADMIN_PASSWORD" | compose exec -T app baza-wiedzy create-admin smoke-admin --password-stdin
token() { curl -sk -c "$JAR" -b "$JAR" "$BASE$1" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | head -n 1; }
status() { curl -sk -o /dev/null -w '%{http_code}' -c "$JAR" -b "$JAR" "$@"; }
expect() {  # expect <label> <wanted> <got>
    if [ "$3" != "$2" ]; then echo "FAILED: $1: expected HTTP $2, got $3"; rm -f "$JAR"; exit 1; fi
    echo "    $1: HTTP $3"
}
expect "admin panel without a session" 303 "$(status "$BASE/admin/users")"
expect "wrong password" 401 "$(status -X POST "$BASE/login" --data-urlencode username=smoke-admin \
    --data-urlencode password=wrong-password --data-urlencode "csrf_token=$(token /login)")"
expect "login" 303 "$(status -X POST "$BASE/login" --data-urlencode username=smoke-admin \
    --data-urlencode "password=$ADMIN_PASSWORD" --data-urlencode "csrf_token=$(token /login)")"
expect "admin panel" 200 "$(status "$BASE/admin/users")"
expect "audit log" 200 "$(status "$BASE/admin/audit")"
expect "logout" 303 "$(status -X POST "$BASE/logout" --data-urlencode "csrf_token=$(token /)")"
expect "admin panel after logout" 303 "$(status "$BASE/admin/users")"
rm -f "$JAR"
echo "==> Smoke test passed: /ready 4 x ok, login, admin panel and logout work"
exit 0
