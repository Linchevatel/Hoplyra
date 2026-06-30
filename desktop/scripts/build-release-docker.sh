#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$ROOT/../.." && pwd)"
IMAGE="${HOPLYRA_BUILD_IMAGE:-hoplyra-desktop-builder}"

CONTAINER_CMD="${CONTAINER_CMD:-}"
if [[ -z "$CONTAINER_CMD" ]]; then
  if command -v docker >/dev/null 2>&1; then
    CONTAINER_CMD=docker
  elif command -v podman >/dev/null 2>&1; then
    CONTAINER_CMD=podman
  else
    echo "ERROR: docker or podman is required." >&2
    exit 1
  fi
fi

echo "==> Building container image ($IMAGE) on Ubuntu 22.04 (glibc 2.35) via $CONTAINER_CMD..."
$CONTAINER_CMD build -t "$IMAGE" -f "$ROOT/Dockerfile.build" "$ROOT"

echo "==> Building AppImage inside container..."
$CONTAINER_CMD run --rm \
  -v "$REPO:/src:rw" \
  -e CI=true \
  -e ELECTRON_BUILDER_CACHE=/tmp/electron-builder-cache \
  "$IMAGE" \
  bash -lc '
    set -euo pipefail
    rm -rf /src/backend/.venv /src/backend/build /src/backend/dist
    cd /src/backend/desktop
    npm ci
    npm run build:native
  '

echo ""
echo "==> Artifacts:"
ls -lh "$ROOT/dist/"*.AppImage "$ROOT/dist/SHA256SUMS" "$ROOT/dist/README.txt" 2>/dev/null || true
