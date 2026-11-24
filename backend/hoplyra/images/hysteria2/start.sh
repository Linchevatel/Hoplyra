#!/bin/sh
set -eu
umask 077
mkdir -p /etc/hysteria
if [ ! -s /etc/hysteria/server.crt ] || [ ! -s /etc/hysteria/server.key ]; then
  openssl req -x509 -nodes -days 3650 -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
    -keyout /etc/hysteria/server.key -out /etc/hysteria/server.crt \
    -subj /CN=bing.com -addext subjectAltName=DNS:bing.com
fi
exec hysteria server -c /etc/hysteria/config.yaml
