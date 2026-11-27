#!/usr/bin/env bash
# Smoke test of the PRODUCTION Compose file with freshly built images (make smoke, CI).
#   1. build app, worker, db, proxy for linux/amd64 and tag them as <prefix>-<name>:<version>
#   2. start docker-compose.yml in a separate Compose project with generated secrets
#   3. run `baza-wiedzy migrate`
#   4. require /ready to answer HTTP 200 with database, redis, schema, worker and ocr-worker
#      all "ok", and the Docker healthcheck of worker and ocr-worker to be "healthy";
#      check the network isolation: the worker reaches db and redis, nothing outside
#   5. create the break-glass admin (generated password, --password-stdin) and, through the
#      proxy: refuse a wrong password, log in, open the admin panel, upload a small TXT file
#      and a small text PDF and an image-only scan (OCR) and wait until the worker has
#      processed them ("ready"; the scan's word is found by search, its text not logged), publish
#      the TXT, find it through the search page, download it (content and safe headers),
#      send a unique "search phrase" to the search page and in
#      the query string, Referer and HX-Current-URL and require that it appears in no
#      container log (proxy, app, worker, db; ADR-025), log out
# Everything (containers, volumes, the temporary env file) is removed at the end.
# Ports: SMOKE_HTTP_PORT / SMOKE_HTTPS_PORT (default 18080 / 18443), so a running dev stack
# on 80/443 is not disturbed. SMOKE_KEEP=1 leaves a passing stack running (for measurements)
# and prints the command that removes it.
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
BACKEND_SUBNET=${SMOKE_BACKEND_SUBNET:-172.30.73.0/24}
PROXY_IP=${SMOKE_PROXY_IP:-172.30.71.10}
SECRET_KEY=$(secret)
POSTGRES_PASSWORD=$(secret)
APP_DB_PASSWORD=$(secret)
REDIS_PASSWORD=$(secret)
DB_MEMORY_LIMIT=1g
POSTGRES_MAX_CONNECTIONS=20
# Small upload limits, so that the limits of the proxy and of the application can be hit.
MAX_UPLOAD_MB=2
UPLOAD_MAX_TOTAL_MB=3
MAX_REQUEST_MB=4
# OCR on hold above one scanned page, so that "Uruchom OCR" can be tested with two pages.
OCR_AUTO_MAX_PAGES=1
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
    if [ "${SMOKE_KEEP:-0}" = "1" ] && [ "$status" -eq 0 ]; then
        echo "==> SMOKE_KEEP=1: the stack keeps running for inspection. Remove it with:"
        echo "    docker compose -p $PROJECT -f docker-compose.yml --env-file $ENV_FILE down -v && rm -f $ENV_FILE"
        exit 0
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
expected = {"database": "ok", "redis": "ok", "schema": "ok", "worker": "ok", "ocr-worker": "ok"}
sys.exit(0 if data.get("status") == "ok" and data.get("checks") == expected else 1)
PY
    then
        echo "$body"
        echo "==> /ready reports 5 x ok"
        ready=1
        break
    fi
    sleep 2
done
if [ "${ready:-0}" != "1" ]; then
    echo "Last /ready answer: ${body:-<none>}"
    exit 1
fi

echo "==> Healthcheck of worker and ocr-worker (own heartbeat)"
for service in worker ocr-worker; do
    health=""
    for _ in $(seq 60); do
        health="$(docker inspect --format '{{.State.Health.Status}}' "$(compose ps -q "$service")")"
        [ "$health" = "healthy" ] && break
        sleep 2
    done
    [ "$health" = "healthy" ] || { echo "FAILED: $service is \"$health\", not healthy"; exit 1; }
    echo "    $service: healthy"
done
# The check is per container: a stopped ocr-worker turns /ready to 503 while worker stays ok.
compose stop ocr-worker >/dev/null
stopped="$(curl -sk --max-time 5 -o /dev/null -w '%{http_code}' "https://localhost:$HTTPS_PORT/ready" || true)"
for _ in $(seq 40); do  # its key disappears at a clean stop; at the latest after 60 s (TTL)
    [ "$stopped" = "503" ] && break
    sleep 2
    stopped="$(curl -sk --max-time 5 -o /dev/null -w '%{http_code}' "https://localhost:$HTTPS_PORT/ready" || true)"
done
[ "$stopped" = "503" ] || { echo "FAILED: /ready is $stopped with ocr-worker stopped, expected 503"; exit 1; }
compose start ocr-worker >/dev/null
echo "    ocr-worker stopped: /ready 503 (ocr-worker: no heartbeat); started again"

echo "==> Network isolation (ADR-024)"
# Worker: database and Redis reachable, nothing outside (TCP to public addresses, DNS of
# public names). App: database and Redis reachable; outbound only reported (AD needs it,
# but CI or an offline server may have no internet at all).
probe() {  # probe <service>: prints one line "name=yes|no" per target
    compose exec -T "$1" python - <<'PY'
import socket
def tcp(host, port):
    try:
        socket.create_connection((host, port), timeout=3).close()
        return "yes"
    except OSError:
        return "no"
def dns(name):
    try:
        socket.getaddrinfo(name, 443)
        return "yes"
    except OSError:
        return "no"
print(f"db={tcp('db', 5432)} redis={tcp('redis', 6379)} "
      f"internet_ip={tcp('1.1.1.1', 443)} internet_ip2={tcp('8.8.8.8', 53)} "
      f"internet_dns={dns('example.com')}")
PY
}
WORKER_NET="$(probe worker)"
OCR_NET="$(probe ocr-worker)"
APP_NET="$(probe app)"
echo "    worker:     $WORKER_NET"
echo "    ocr-worker: $OCR_NET"
echo "    app:        $APP_NET"
for net in "$WORKER_NET" "$OCR_NET"; do
    case "$net" in
        "db=yes redis=yes internet_ip=no internet_ip2=no internet_dns=no") ;;
        *) echo "FAILED: worker and ocr-worker must reach db and redis and nothing outside"; exit 1 ;;
    esac
done
case "$APP_NET" in
    "db=yes redis=yes"*) ;;
    *) echo "FAILED: the app must reach db and redis"; exit 1 ;;
esac

echo "==> Images: no HTML selector libraries in app; soupsieve >= 4.0 in worker (GHSA-mmhr-3c5m-f64f)"
compose exec -T app python -c "import importlib.util as u, sys; sys.exit(any(u.find_spec(m) for m in ('bs4', 'soupsieve')))" \
    || { echo "FAILED: the app image contains bs4 or soupsieve (they belong to the worker extra only)"; exit 1; }
SOUPSIEVE="$(compose exec -T worker python -c "import importlib.metadata as m; print(m.version('soupsieve'))")"
case "$SOUPSIEVE" in
    [4-9].*|[1-9][0-9].*) echo "    app: no bs4/soupsieve; worker: soupsieve $SOUPSIEVE" ;;
    *) echo "FAILED: the worker image has soupsieve $SOUPSIEVE (needs >= 4.0.0)"; exit 1 ;;
esac
TESSERACT="$(compose exec -T worker tesseract --version 2>&1 | head -n 1)"
LANGS="$(compose exec -T worker tesseract --list-langs 2>&1 | tr '\n' ' ')"
case "$TESSERACT $LANGS" in
    "tesseract 5."*" eng"*" osd"*" pol"*) echo "    worker: $TESSERACT, languages: eng osd pol" ;;
    *) echo "FAILED: unexpected Tesseract in the worker image: $TESSERACT / $LANGS"; exit 1 ;;
esac
if compose exec -T worker sh -c 'command -v gs' >/dev/null 2>&1; then
    echo "FAILED: Ghostscript is in the worker image (not needed, AGPL; ADR-031)"; exit 1
fi
echo "    worker: no Ghostscript"

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
upload_and_wait() {  # upload_and_wait <file> <name> <content type>
    local headers doc_path fragment processed=""
    headers="$(mktemp)"
    expect "upload $2" 204 "$(status -D "$headers" -X POST "$BASE/documents/upload" \
        -H "HX-Request: true" -H "X-CSRF-Token: $(token /documents/upload)" \
        -F "file=@$1;filename=$2;type=$3")"
    doc_path="$(sed -n 's/^[Hh][Xx]-[Rr]edirect: \([^?[:space:]]*\).*/\1/p' "$headers")"
    rm -f "$headers"
    if [ -z "$doc_path" ]; then echo "FAILED: no HX-Redirect after the upload"; rm -f "$JAR"; exit 1; fi
    for _ in $(seq 60); do
        fragment="$(curl -sk -c "$JAR" -b "$JAR" -H "HX-Request: true" "$BASE$doc_path/status")"
        case "$fragment" in
            *badge-done*) processed=done; break ;;
            *badge-failed*) processed=failed; break ;;
        esac
        sleep 2
    done
    if [ "$processed" != "done" ]; then
        echo "FAILED: $2 is not \"ready\" (state: ${processed:-still waiting})"
        rm -f "$JAR"; exit 1
    fi
    echo "    $2: ready"
    LAST_DOC="$doc_path"
    local version
    version="$(printf '%s' "$fragment" | sed -n 's|.*/ingestion/versions/\([0-9a-f-]*\)/parts.*|\1|p' | head -n 1)"
    expect "extracted parts of $2" 200 "$(status "$BASE/ingestion/versions/$version/parts")"
}

echo "==> Upload a small TXT and a small text PDF, wait until both are processed"
SAMPLE_TXT="$(mktemp)"
SAMPLE_PDF="$(mktemp)"
printf 'Test dymny: zażółć gęślą jaźń. Przegląd urządzeń w spółdzielni mieszkaniowej.\n' > "$SAMPLE_TXT"
# A one-page PDF with a text layer (standard font, ASCII), written without any library.
python3 - "$SAMPLE_PDF" <<'PY'
import sys
stream = b"BT /F1 18 Tf 72 720 Td (Smoke test PDF with a text layer) Tj ET"
objects = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
    b"/Resources << /Font << /F1 5 0 R >> >> >>",
    b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
]
out, offsets = bytearray(b"%PDF-1.4\n"), []
for number, body in enumerate(objects, start=1):
    offsets.append(len(out))
    out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
xref = len(out)
out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
open(sys.argv[1], "wb").write(out)
PY
upload_and_wait "$SAMPLE_PDF" smoke.pdf application/pdf

echo "==> OCR: a scanned PDF (image only, made in the worker image) is recognised"
SAMPLE_SCAN="$(mktemp)"
compose exec -T worker python - > "$SAMPLE_SCAN" <<'PY'
import io, sys
from PIL import Image, ImageDraw, ImageFont
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 34)
image = Image.new("L", (1654, 2339), 255)
draw = ImageDraw.Draw(image)
for i, line in enumerate(["Protokół odbioru urządzeń chłodniczych", "Skanowany dokument testowy"]):
    draw.text((160, 220 + i * 75), line, fill=0, font=font)
out = io.BytesIO()
image.save(out, "PDF", resolution=200)
sys.stdout.buffer.write(out.getvalue())
PY
upload_and_wait "$SAMPLE_SCAN" skan.pdf application/pdf
SCAN_DOC="$LAST_DOC"
rm -f "$SAMPLE_SCAN"
page="$(curl -sk -c "$JAR" -b "$JAR" "$BASE/search?q=ch%C5%82odniczych")"
case "$page" in
    *"href=\"$SCAN_DOC\""*) echo "    search for a word of the scan: found" ;;
    *) echo "FAILED: search does not find the word recognised by OCR"; exit 1 ;;
esac
case "$page" in
    *"badge-ocr"*"tekst z OCR"*) echo "    search result: labelled \"tekst z OCR\"" ;;
    *) echo "FAILED: the search result does not say \"tekst z OCR\""; exit 1 ;;
esac
case "$(curl -sk -c "$JAR" -b "$JAR" "$BASE$SCAN_DOC")" in
    *"tekst z OCR"*) echo "    document page: \"tekst z OCR\"" ;;
    *) echo "FAILED: the document page does not say \"tekst z OCR\""; exit 1 ;;
esac
if compose logs --no-color worker ocr-worker 2>&1 | grep -qi "odbioru\|chłodniczych"; then
    echo "FAILED: recognised text appears in the worker or ocr-worker log"; exit 1
fi
echo "    worker and ocr-worker logs: no recognised text"
compose logs --no-color ocr-worker 2>&1 | grep -q "ocr: done version=" \
    || { echo "FAILED: the OCR did not run in ocr-worker"; exit 1; }
if compose logs --no-color worker 2>&1 | grep -q "ocr: done version="; then
    echo "FAILED: OCR ran in the ordinary worker (it must only consume the queue ingestion)"; exit 1
fi
echo "    OCR ran in ocr-worker, not in worker"

echo "==> OCR: two scanned pages wait on hold (OCR_AUTO_MAX_PAGES=1) until \"Uruchom OCR\""
SAMPLE_SCAN="$(mktemp)"
compose exec -T worker python - > "$SAMPLE_SCAN" <<'PY'
import io, sys
from PIL import Image, ImageDraw, ImageFont
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 34)
pages = []
for text in ("Wstrzymany skan strona pierwsza", "Wstrzymany skan strona druga"):
    image = Image.new("L", (1654, 2339), 255)
    ImageDraw.Draw(image).text((160, 220), text, fill=0, font=font)
    pages.append(image)
out = io.BytesIO()
pages[0].save(out, "PDF", resolution=200, save_all=True, append_images=pages[1:])
sys.stdout.buffer.write(out.getvalue())
PY
HEADERS="$(mktemp)"
expect "upload of a two-page scan" 204 "$(status -D "$HEADERS" -X POST "$BASE/documents/upload" \
    -H "HX-Request: true" -H "X-CSRF-Token: $(token /documents/upload)" \
    -F "file=@$SAMPLE_SCAN;filename=duzy-skan.pdf;type=application/pdf")"
HOLD_DOC="$(sed -n 's/^[Hh][Xx]-[Rr]edirect: \([^?[:space:]]*\).*/\1/p' "$HEADERS")"
rm -f "$SAMPLE_SCAN" "$HEADERS"
fragment=""
for _ in $(seq 60); do
    fragment="$(curl -sk -c "$JAR" -b "$JAR" -H "HX-Request: true" "$BASE$HOLD_DOC/status")"
    case "$fragment" in *"OCR wstrzymany"*) break ;; esac
    sleep 2
done
case "$fragment" in
    *"OCR wstrzymany"*"Uruchom OCR"*) echo "    status: \"OCR wstrzymany (duży dokument)\" with \"Uruchom OCR\"" ;;
    *) echo "FAILED: a two-page scan is not on hold"; exit 1 ;;
esac
HOLD_VERSION="$(printf '%s' "$fragment" | sed -n 's|.*/ingestion/versions/\([0-9a-f-]*\)/ocr".*|\1|p' | head -n 1)"
expect "Uruchom OCR" 303 "$(status -X POST "$BASE/ingestion/versions/$HOLD_VERSION/ocr" \
    --data-urlencode "csrf_token=$(token "$HOLD_DOC")")"
processed=""
for _ in $(seq 60); do
    case "$(curl -sk -c "$JAR" -b "$JAR" -H "HX-Request: true" "$BASE$HOLD_DOC/status")" in
        *badge-done*) processed=done; break ;;
    esac
    sleep 2
done
[ "$processed" = "done" ] || { echo "FAILED: OCR started with the button did not finish"; exit 1; }
echo "    after \"Uruchom OCR\": ready"

echo "==> Several files in one form without JavaScript (token as the first field), limits"
MULTI_A="$(mktemp)"; MULTI_B="$(mktemp)"; BIG="$(mktemp)"; HUGE="$(mktemp)"; HEADERS="$(mktemp)"
printf 'Pierwszy plik z wielu: umowa najmu.\n' > "$MULTI_A"
printf 'Drugi plik z wielu: protokół.\n' > "$MULTI_B"
TOKEN="$(token /documents/upload)"
expect "two files, plain form" 303 "$(status -D "$HEADERS" -X POST "$BASE/documents/upload" \
    -F "csrf_token=$TOKEN" -F "files=@$MULTI_A;filename=smoke-a.txt;type=text/plain" \
    -F "files=@$MULTI_B;filename=smoke-b.txt;type=text/plain")"
BATCH="$(sed -n 's/^[Ll]ocation: \(\/documents?batch=[0-9a-f]*\).*/\1/p' "$HEADERS" | tr -d '\r')"
case "$(curl -sk -c "$JAR" -b "$JAR" "$BASE$BATCH")" in
    *"Wgrano: 2"*) echo "    result table: Wgrano: 2" ;;
    *) echo "FAILED: no result table with \"Wgrano: 2\" after a plain multi-file form"; exit 1 ;;
esac
expect "plain form with the token after the files" 403 "$(status -X POST "$BASE/documents/upload" \
    -F "files=@$MULTI_A;filename=smoke-c.txt;type=text/plain" -F "csrf_token=$TOKEN")"
head -c $((3 * 1024 * 1024 + 300 * 1024)) /dev/zero | tr '\0' 'a' > "$BIG"   # 3.3 MB: app limit 3
page="$(curl -sk -c "$JAR" -b "$JAR" -w '%{http_code}' -X POST "$BASE/documents/upload" \
    -H "HX-Request: true" -H "X-CSRF-Token: $TOKEN" -F "files=@$BIG;filename=big.txt")"
case "$page" in
    *"razem większe niż dozwolony rozmiar (3 MB)"*413) echo "    3.3 MB form: refused by the application with a message (413)" ;;
    *) echo "FAILED: a form above UPLOAD_MAX_TOTAL_MB is not refused readably"; exit 1 ;;
esac
head -c $((5 * 1024 * 1024)) /dev/zero | tr '\0' 'a' > "$HUGE"   # 5 MB: proxy limit 4
page="$(curl -sk -c "$JAR" -b "$JAR" -w '%{http_code}' -X POST "$BASE/documents/upload" \
    -H "X-CSRF-Token: $TOKEN" -F "files=@$HUGE;filename=huge.txt")"
case "$page" in
    *"razem większe niż dozwolony rozmiar"*413) echo "    5 MB form with Content-Length: refused by the application with a message (413)" ;;
    *) echo "FAILED: a 5 MB form is not refused readably: ${page: -60}"; exit 1 ;;
esac
# Without Content-Length (chunked): the application or, past MAX_REQUEST_MB, the proxy's own page.
page="$(curl -sk -c "$JAR" -b "$JAR" -w '%{http_code}' -X POST "$BASE/documents/upload" \
    -H "Transfer-Encoding: chunked" -H "X-CSRF-Token: $TOKEN" -F "files=@$HUGE;filename=huge.txt")"
case "$page" in
    *"razem większe niż dozwolony rozmiar"*413|*"Za duże żądanie"*413) echo "    5 MB chunked form: readable message (413)" ;;
    *) echo "FAILED: an oversized chunked form gives no readable message: ${page: -60}"; exit 1 ;;
esac
rm -f "$MULTI_A" "$MULTI_B" "$BIG" "$HUGE" "$HEADERS"
upload_and_wait "$SAMPLE_TXT" "smoke Łódź.txt" text/plain
TXT_DOC="$LAST_DOC"

echo "==> Publish the TXT and download it (headers of a safe download)"
expect "publish" 303 "$(status -X POST "$BASE$TXT_DOC/lifecycle" --data-urlencode action=publish \
    --data-urlencode expected=draft --data-urlencode "csrf_token=$(token "$TXT_DOC")")"
DOWNLOAD="$(mktemp)"
HEADERS="$(mktemp)"
expect "download" 200 "$(curl -sk -c "$JAR" -b "$JAR" -D "$HEADERS" -o "$DOWNLOAD" \
    -w '%{http_code}' "$BASE$TXT_DOC/download")"
cmp -s "$DOWNLOAD" "$SAMPLE_TXT" || { echo "FAILED: the downloaded file differs from the upload"; exit 1; }
for wanted in "content-type: application/octet-stream" "x-content-type-options: nosniff" \
    "content-disposition: attachment; filename=\"smoke Lodz.txt\"; filename\*=UTF-8''smoke%20%C5%81%C3%B3d%C5%BA.txt" \
    "content-security-policy: default-src 'none'; sandbox"; do
    tr -d '\r' < "$HEADERS" | grep -qi "^$wanted\$" || { echo "FAILED: download header missing: $wanted"; exit 1; }
done
echo "    download headers: ok"
# Search finds the published TXT through the proxy: an inflected form (Polish dictionary) and
# a query without diacritics (simple_unaccent).
for query in "urz%C4%85dzenie" "spoldzielni"; do
    page="$(curl -sk -c "$JAR" -b "$JAR" "$BASE/search?q=$query")"
    case "$page" in
        *"href=\"$TXT_DOC\""*) echo "    search $query: found" ;;
        *) echo "FAILED: search for $query does not find the published TXT; index state:"
           compose exec -T app baza-wiedzy search-check || true
           exit 1 ;;
    esac
done
rm -f "$SAMPLE_TXT" "$SAMPLE_PDF" "$DOWNLOAD"

echo "==> Search phrases never reach a log (query string, Referer, HX-Current-URL, redirects)"
PHRASE="tajnafraza$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
status -H "Referer: $BASE/search?q=$PHRASE" -H "HX-Current-URL: $BASE/search?q=$PHRASE" \
    "$BASE/documents?q=$PHRASE&page=1" > /dev/null
expect "search with the phrase" 200 "$(status "$BASE/search?q=$PHRASE&sort=date")"
expect "search with an unbalanced quote" 200 "$(status "$BASE/search?q=%22$PHRASE")"
curl -sk -D "$HEADERS" -o /dev/null "$BASE/documents?q=$PHRASE"  # no session: login redirect
grep -qi "^location: /login?next=.*$PHRASE" "$HEADERS" \
    || { echo "FAILED: the login redirect does not carry the request (test is not meaningful)"; exit 1; }
tr -d '\r' < "$HEADERS" | grep -qi "^referrer-policy: same-origin$" \
    || { echo "FAILED: Referrer-Policy: same-origin missing"; exit 1; }
rm -f "$HEADERS"
# Wait until the proxy has written the filtered request (its log is flushed asynchronously).
for _ in $(seq 1 15); do
    compose logs --no-color proxy 2>&1 | grep -q '/documents?\[removed\]' && break
    sleep 1
done
if compose logs --no-color proxy app worker ocr-worker db 2>&1 | grep -q "$PHRASE"; then
    echo "FAILED: the search phrase appears in a container log:"
    compose logs --no-color proxy app worker ocr-worker db 2>&1 | grep -n "$PHRASE" | cut -c1-200
    exit 1
fi
# File names and CSRF tokens stay out of the proxy log as well (Content-Disposition, X-CSRF-Token).
if compose logs --no-color proxy 2>&1 | grep -qi 'smoke Lodz\|smoke%20\|Content-Disposition\|X-Csrf-Token'; then
    echo "FAILED: the proxy log contains a file name or a CSRF token"; exit 1
fi
compose logs --no-color proxy 2>&1 | grep -q '/documents?\[removed\]' \
    || { echo "FAILED: the proxy access log does not show the filtered request"; exit 1; }
echo "    proxy, app, worker, ocr-worker, db logs: no search phrase; proxy log: no file name, no CSRF token"

expect "logout" 303 "$(status -X POST "$BASE/logout" --data-urlencode "csrf_token=$(token /)")"
expect "admin panel after logout" 303 "$(status "$BASE/admin/users")"
rm -f "$JAR"
echo "==> Smoke test passed: /ready 5 x ok, worker healthchecks, network isolation, login, admin panel, TXT, PDF and scan (OCR in ocr-worker; on hold + \"Uruchom OCR\") processed to \"ready\", multi-file form without JavaScript and upload limits, publish, search and download, no search phrase in logs, logout"
exit 0
