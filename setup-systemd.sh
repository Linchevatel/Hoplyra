#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv/bin/python"
RUN="$ROOT/run.py"
ENV_FILE="$ROOT/.env"
SERVICE_NAME="hoplyra"

if [[ ! -x "$VENV" ]]; then
  echo "Сначала выполните: make install" >&2
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

HOST="${HOPLYRA_HOST:-0.0.0.0}"
PORT="${HOPLYRA_PORT:-8787}"

if [[ "$(id -u)" -eq 0 ]]; then
  UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
  SYSTEMCTL=(systemctl)
  WANTED_BY="multi-user.target"
else
  UNIT_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/${SERVICE_NAME}.service"
  SYSTEMCTL=(systemctl --user)
  WANTED_BY="default.target"
  mkdir -p "$(dirname "$UNIT_PATH")"
fi

write_unit() {
  cat >"$UNIT_PATH" <<EOF
[Unit]
Description=Hoplyra VPN Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${ROOT}
EnvironmentFile=-${ENV_FILE}
ExecStart=${VENV} ${RUN}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=${WANTED_BY}
EOF
}

print_links() {
  local display_host="$HOST"
  if [[ "$HOST" == "0.0.0.0" || "$HOST" == "::" ]]; then
    display_host="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    [[ -n "$display_host" ]] || display_host="127.0.0.1"
  fi
  local scheme="http"
  if [[ -n "${HOPLYRA_SSL_CERTFILE:-${SSL_CERTFILE:-}}" && -n "${HOPLYRA_SSL_KEYFILE:-${SSL_KEYFILE:-}}" ]]; then
    scheme="https"
  fi
  local base="${scheme}://${display_host}:${PORT}"
  echo ""
  echo "Hoplyra запущен."
  echo "  Дашборд:  ${base}/"
  echo "  API:      ${base}/api/health"
  echo ""
  echo "  Вход:     admin / admin"
  echo "  (смените пароль в «Настройки» после первого входа)"
  echo ""
  echo "Управление: ${SYSTEMCTL[*]} status ${SERVICE_NAME}"
}

wait_health() {
  local scheme="http"
  local curl_opts=("-sf")
  if [[ -n "${HOPLYRA_SSL_CERTFILE:-${SSL_CERTFILE:-}}" && -n "${HOPLYRA_SSL_KEYFILE:-${SSL_KEYFILE:-}}" ]]; then
    scheme="https"
    curl_opts+=("-k")
  fi
  local i
  for i in $(seq 1 40); do
    if curl "${curl_opts[@]}" "${scheme}://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  echo "Сервис создан, но API пока не отвечает. Проверьте: ${SYSTEMCTL[*]} status ${SERVICE_NAME}" >&2
  return 1
}

case "${1:-start}" in
  start)
    write_unit
    "${SYSTEMCTL[@]}" daemon-reload
    "${SYSTEMCTL[@]}" enable --now "$SERVICE_NAME"
    wait_health || true
    print_links
    if [[ "$(id -u)" -ne 0 ]]; then
      if command -v loginctl >/dev/null 2>&1 && loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q 'Linger=no'; then
        echo "Автозапуск после перезагрузки (без входа): sudo loginctl enable-linger $USER"
      fi
    fi
    ;;
  stop)
    "${SYSTEMCTL[@]}" stop "$SERVICE_NAME" 2>/dev/null || true
    echo "Hoplyra остановлен."
    ;;
  status)
    "${SYSTEMCTL[@]}" status "$SERVICE_NAME" --no-pager || true
    ;;
  *)
    echo "Usage: $0 [start|stop|status]" >&2
    exit 1
    ;;
esac
