#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/.."
DESKTOP="$ROOT"
RESOURCES="$DESKTOP/resources/backend"

cd "$BACKEND"

if [[ ! -x .venv/bin/python ]]; then
  echo "==> Creating backend venv..."
  PYTHON="${PYTHON:-python3}"
  "$PYTHON" -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

if [[ ! -d ui/dist ]]; then
  echo "==> ui/dist missing — run: npm run build:frontend" >&2
  exit 1
fi

echo "==> Installing PyInstaller..."
.venv/bin/pip install -q -r requirements-build.txt

echo "==> Building hoplyra-backend binary..."
rm -rf build dist
.venv/bin/pyinstaller --noconfirm hoplyra-backend.spec

mkdir -p "$RESOURCES"
rm -rf "$RESOURCES"/*
cp -a dist/hoplyra-backend "$RESOURCES/"
chmod +x "$RESOURCES/hoplyra-backend"

echo "==> Backend bundle ready at $RESOURCES/hoplyra-backend"
