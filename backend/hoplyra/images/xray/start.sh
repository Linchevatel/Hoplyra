#!/bin/sh
set -e

DIR=/opt/hoplyra/xray
CFG="${XRAY_CONFIG:-$DIR/config.json}"

if [ -n "${TLS_CN:-}" ] && [ ! -f "$DIR/cert.pem" ]; then
  openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
    -keyout "$DIR/key.pem" -out "$DIR/cert.pem" -subj "/CN=$TLS_CN"
fi

exec xray run -c "$CFG"
