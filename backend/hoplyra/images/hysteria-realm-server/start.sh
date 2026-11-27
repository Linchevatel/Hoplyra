#!/bin/sh
set -eu
umask 077
CERT_DIR="/etc/hysteria-realm"
mkdir -p "$CERT_DIR"

if [ ! -s "$CERT_DIR/server.crt" ] || [ ! -s "$CERT_DIR/server.key" ]; then
  openssl req -x509 -nodes -days 3650 -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
    -keyout "$CERT_DIR/server.key" -out "$CERT_DIR/server.crt" \
    -subj "/CN=hysteria-realm" -addext "subjectAltName=DNS:hysteria-realm,IP:127.0.0.1"
fi

export HYSTERIA_REALM_CERT="${HYSTERIA_REALM_CERT:-$CERT_DIR/server.crt}"
export HYSTERIA_REALM_KEY="${HYSTERIA_REALM_KEY:-$CERT_DIR/server.key}"

exec /usr/local/bin/hysteria-realm-server "$@"
