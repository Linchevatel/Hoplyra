#!/bin/sh
set -e

G=/opt/hoplyra/gateway
[ -f "$G/start.sh" ] || { echo "missing $G/start.sh"; exit 1; }

if [ -f "$G/torrc" ]; then
  mkdir -p /etc/tor /var/lib/tor
  cp "$G/torrc" /etc/tor/torrc
  chown -R tor:tor /var/lib/tor 2>/dev/null || true
fi

exec sh "$G/start.sh"
