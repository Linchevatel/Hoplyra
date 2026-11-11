#!/usr/bin/env bash
set -euo pipefail

HOST="${1:-root@144.172.110.70}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

rsync -avz \
  --exclude-from="$ROOT/backend/.rsyncignore" \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'data/' \
  "$ROOT/backend/" "$HOST:/root/Hoplyra/"

ssh "$HOST" 'systemctl restart hoplyra && sleep 2 && systemctl is-active hoplyra'
