#!/usr/bin/env bash
# Build an offline bundle of ALL images a server needs (docs: deployment-operations.md,
# "Offline server"). Run on a machine with internet access and `docker login ghcr.io`.
#
#   scripts/offline_bundle.sh <version> <image-prefix> [output-dir]
#   e.g. scripts/offline_bundle.sh 1.2.0 ghcr.io/acme/baza-wiedzy
#
# The list comes from docker-compose.yml (including the optional antivirus profile), so it
# always matches the Compose file of this version: <prefix>-{app,worker,db,proxy}:<version>,
# redis and clamav pinned by digest. Output: baza-wiedzy-images-<version>.tar, its SHA-256 and
# the list of image references with their digests.
set -euo pipefail

VERSION="${1:?usage: offline_bundle.sh <version> <image-prefix> [output-dir]}"
PREFIX="${2:?usage: offline_bundle.sh <version> <image-prefix> [output-dir]}"
OUT="${3:-.}"
# Testing only: OFFLINE_BUNDLE_NO_PULL=1 uses images already present locally (no registry).
NO_PULL="${OFFLINE_BUNDLE_NO_PULL:-0}"
cd "$(dirname "$0")/.."

# Dummy secrets only satisfy the required variables; nothing is started.
mapfile -t IMAGES < <(
    IMAGE_PREFIX="$PREFIX" APP_VERSION="$VERSION" POSTGRES_PASSWORD=x APP_DB_PASSWORD=x \
    REDIS_PASSWORD=x SECRET_KEY=x \
    docker compose -f docker-compose.yml --env-file /dev/null --profile antivirus config --images
)

SAVE=()
for ref in "${IMAGES[@]}"; do
    if [ "$NO_PULL" = "1" ]; then
        docker image inspect "$ref" >/dev/null
    else
        echo "==> pull $ref"
        docker pull --platform linux/amd64 -q "$ref" >/dev/null
    fi
    # Save by tag (name:tag). A reference saved only by digest loses its name on `docker load`;
    # saved by tag it can still be resolved by the pinned digest after loading.
    tag_ref="${ref%@*}"
    [ "$tag_ref" != "$ref" ] && docker tag "$ref" "$tag_ref"
    SAVE+=("$tag_ref")
done

mkdir -p "$OUT"
TAR="$OUT/baza-wiedzy-images-$VERSION.tar"
LIST="$OUT/baza-wiedzy-images-$VERSION.txt"
echo "==> save ${#SAVE[@]} images to $TAR"
docker save -o "$TAR" "${SAVE[@]}"
{
    for ref in "${IMAGES[@]}"; do
        echo "$ref $(docker image inspect --format '{{.Id}}' "$ref")"
    done
} > "$LIST"
(cd "$OUT" && sha256sum "$(basename "$TAR")" > "$(basename "$TAR").sha256")
echo "==> done:"
ls -l "$TAR" "$TAR.sha256" "$LIST"
