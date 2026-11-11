#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
VENV="$ROOT/.venv/bin/python"

if [[ ! -x "$VENV" ]]; then
  echo "bootstrap-env: venv not found, run pip install first" >&2
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  echo "==> .env already exists — skipped"
  exit 0
fi

SESSION_SECRET="$("$VENV" -c "import secrets; print(secrets.token_hex(32))")"
FERNET_KEY="$("$VENV" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")"

cat >"$ENV_FILE" <<EOF
HOPLYRA_HOST=0.0.0.0
HOPLYRA_PORT=8787
HOPLYRA_ADMIN_USER=admin
HOPLYRA_ADMIN_PASSWORD=admin
HOPLYRA_SESSION_SECRET=${SESSION_SECRET}
HOPLYRA_SECRET_KEY=${FERNET_KEY}
EOF

chmod 600 "$ENV_FILE" 2>/dev/null || true
echo "==> Created $ENV_FILE with default panel login admin / admin"

if [[ -x "$ROOT/scripts/generate-ssl.sh" ]]; then
  bash "$ROOT/scripts/generate-ssl.sh" || true
fi

