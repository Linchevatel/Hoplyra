#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
VERSION="$(node -p "require('$ROOT/package.json').version")"
ARTIFACT_GLOB="Hoplyra-${VERSION}-*.AppImage"

cd "$ROOT"

echo "==> Cleaning previous build artifacts..."
rm -rf "$ROOT/release" "$ROOT/resources/backend" "$ROOT/dist"
mkdir -p "$DIST"

echo "==> Building frontend..."
npm run build:frontend

echo "==> Building backend binary..."
bash scripts/build-backend.sh

echo "==> Packaging AppImage..."
npx electron-builder --linux AppImage --publish never

BUILT="$(find "$ROOT/dist" -maxdepth 1 -name "$ARTIFACT_GLOB" -print -quit 2>/dev/null || true)"
if [[ -z "$BUILT" || ! -f "$BUILT" ]]; then
  echo "ERROR: AppImage not found after build (expected dist/$ARTIFACT_GLOB)" >&2
  exit 1
fi

ARTIFACT="$(basename "$BUILT")"
FINAL="$DIST/$ARTIFACT"
if [[ "$BUILT" != "$FINAL" ]]; then
  mv "$BUILT" "$FINAL"
fi

chmod +x "$FINAL"
rm -rf "$ROOT/dist/linux-unpacked" "$ROOT/dist/builder-"*.yml "$ROOT/dist/builder-"*.yaml "$ROOT/dist/"*.blockmap 2>/dev/null || true
rm -rf "$ROOT/release" "$ROOT/resources"

SHA256="$(sha256sum "$FINAL" | awk '{print $1}')"
echo "$SHA256  $ARTIFACT" > "$DIST/SHA256SUMS"

cat > "$DIST/README.txt" <<EOF
Hoplyra ${VERSION} — Linux x86_64 AppImage

Run:
  chmod +x ${ARTIFACT}
  ./${ARTIFACT}

Requirements:
  - 64-bit Linux with glibc 2.35+ (Ubuntu 22.04+, Debian 12+, Fedora 36+, etc.)
  - libfuse.so.2 (package: libfuse2) on Ubuntu 22.04/24.04
  - If FUSE is unavailable: ./${ARTIFACT} --appimage-extract-and-run

SHA256: ${SHA256}
EOF

echo ""
echo "==> Done: $FINAL"
echo "    Size: $(du -h "$FINAL" | awk '{print $1}')"
echo "    SHA256: $SHA256"
