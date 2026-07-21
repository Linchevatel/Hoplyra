#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSL_DIR="${ROOT}/data/ssl"
CERT="${SSL_DIR}/cert.pem"
KEY="${SSL_DIR}/key.pem"
ENV_FILE="${ROOT}/.env"

mkdir -p "$SSL_DIR"

if [[ -f "$CERT" && -f "$KEY" ]]; then
  echo "Сертификаты уже существуют в ${SSL_DIR}:"
  echo "  Cert: $CERT"
  echo "  Key:  $KEY"
else
  echo "Генерация самоподписанного SSL-сертификата (365 дней)..."
  openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$KEY" \
    -out "$CERT" \
    -subj "/CN=Hoplyra/O=SelfHosted/OU=VPN" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" 2>/dev/null || \
  openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$KEY" \
    -out "$CERT" \
    -subj "/CN=Hoplyra/O=SelfHosted/OU=VPN"

  chmod 600 "$KEY"
  echo "Сертификаты успешно созданы:"
  echo "  Cert: $CERT"
  echo "  Key:  $KEY"
fi

if [[ -f "$ENV_FILE" ]]; then
  if grep -q "HOPLYRA_SSL_CERTFILE=" "$ENV_FILE"; then
    sed -i "s|^#*\s*HOPLYRA_SSL_CERTFILE=.*|HOPLYRA_SSL_CERTFILE=${CERT}|" "$ENV_FILE"
    sed -i "s|^#*\s*HOPLYRA_SSL_KEYFILE=.*|HOPLYRA_SSL_KEYFILE=${KEY}|" "$ENV_FILE"
  else
    echo "" >> "$ENV_FILE"
    echo "HOPLYRA_SSL_CERTFILE=${CERT}" >> "$ENV_FILE"
    echo "HOPLYRA_SSL_KEYFILE=${KEY}" >> "$ENV_FILE"
  fi
  echo "Пути к сертификатам прописаны в ${ENV_FILE}"
else
  echo "Создайте .env и добавьте:"
  echo "HOPLYRA_SSL_CERTFILE=${CERT}"
  echo "HOPLYRA_SSL_KEYFILE=${KEY}"
fi
