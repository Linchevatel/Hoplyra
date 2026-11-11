#!/usr/bin/env bash
# Локальный запуск Hoplyra: API :8787 + UI :5173
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV="$BACKEND/.venv"

venv_ok() {
  [[ -x "$VENV/bin/python" ]] && "$VENV/bin/python" -c "import uvicorn" 2>/dev/null
}

if ! venv_ok; then
  echo "==> Пересоздаём backend/.venv (старый venv сломан после переноса каталогов)"
  rm -rf "$VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -r "$BACKEND/requirements.txt"
fi

if [[ ! -d "$FRONTEND/node_modules" ]]; then
  echo "==> npm install"
  (cd "$FRONTEND" && npm install)
fi

backend_up() {
  curl -sf http://127.0.0.1:8787/api/health >/dev/null 2>&1
}

stop_backend() {
  if command -v fuser >/dev/null 2>&1; then
    fuser -k 8787/tcp 2>/dev/null || true
  fi
  pkill -f "$BACKEND/run.py" 2>/dev/null || true
  sleep 0.3
}

frontend_up() {
  curl -sf http://127.0.0.1:5173/ >/dev/null 2>&1
}

echo "==> Перезапуск API (подхват свежего кода)..."
stop_backend
nohup "$VENV/bin/python" "$BACKEND/run.py" > /tmp/hoplyra-backend.log 2>&1 &
for _ in $(seq 1 20); do
  backend_up && break
  sleep 0.25
done
backend_up || { echo "Backend не стартовал, см. /tmp/hoplyra-backend.log"; exit 1; }
echo "Backend: http://127.0.0.1:8787/api/health"

if frontend_up; then
  echo "Frontend уже запущен на http://127.0.0.1:5173"
else
  echo "==> Запуск UI..."
  nohup npm --prefix "$FRONTEND" run dev -- --host 127.0.0.1 --port 5173 \
    > /tmp/hoplyra-frontend.log 2>&1 &
  for _ in $(seq 1 40); do
    frontend_up && break
    sleep 0.25
  done
  frontend_up || { echo "Frontend не стартовал, см. /tmp/hoplyra-frontend.log"; exit 1; }
  echo "Frontend: http://127.0.0.1:5173/"
fi

echo ""
echo "Откройте в браузере: http://127.0.0.1:5173/"
