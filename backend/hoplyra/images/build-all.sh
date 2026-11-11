#!/usr/bin/env bash
set -euo pipefail

BACKEND="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGES_DIR="$(cd "$(dirname "$0")" && pwd)"
CACHE="${HOPLYRA_IMAGE_CACHE:-$BACKEND/.image-cache}"
MARKER="$CACHE/.ready"
export PYTHONPATH="$BACKEND"

runtime() {
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then echo docker; return; fi
  if command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then echo podman; return; fi
  echo "[hoplyra-images] podman/docker недоступен — образы будут собираться на VPS при деплое" >&2
  exit 0
}

RT="$(runtime)"
mkdir -p "$CACHE"

need_rebuild=0
if [[ ! -f "$MARKER" ]]; then
  need_rebuild=1
else
  while IFS= read -r line; do
    bundle="${line%% *}"
    image="${line##* }"
    archive="$CACHE/${image//:/_}.tar.gz"
    if [[ ! -f "$archive" ]]; then
      need_rebuild=1
      break
    fi
    newest_src="$(find "$IMAGES_DIR/$bundle" -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f1)"
    archive_mtime="$(stat -c %Y "$archive" 2>/dev/null || echo 0)"
    if [[ -n "$newest_src" ]] && awk "BEGIN {exit !($newest_src > $archive_mtime)}"; then
      need_rebuild=1
      break
    fi
  done < <(python3 -c "from hoplyra.image_catalog import all_images
for img, bundle in all_images():
    print(f'{bundle} {img}')")
fi

if [[ "$need_rebuild" -eq 0 ]]; then
  echo "[hoplyra-images] кэш актуален: $CACHE"
  exit 0
fi

echo "[hoplyra-images] сборка образов ($RT) → $CACHE"

while IFS= read -r line; do
  bundle="${line%% *}"
  image="${line##* }"
  ctx="$IMAGES_DIR/$bundle"
  archive="$CACHE/${image//:/_}.tar.gz"
  echo "  → build $image"
  $RT build -t "$image" "$ctx"
  echo "  → save $archive"
  $RT save "$image" | gzip -9 > "$archive"
done < <(python3 -c "from hoplyra.image_catalog import all_images
for img, bundle in all_images():
    print(f'{bundle} {img}')")

date -Iseconds > "$MARKER"
echo "[hoplyra-images] готово: $(ls -1 "$CACHE"/*.tar.gz 2>/dev/null | wc -l) образов"
