#!/bin/sh
set -e

DIR=/opt/hoplyra/tor
mkdir -p /var/lib/tor /etc/tor
chown -R tor:tor /var/lib/tor 2>/dev/null || true

if [ -f "$DIR/torrc" ]; then
  cp "$DIR/torrc" /etc/tor/torrc
fi

su-exec tor tor -f /etc/tor/torrc &
exec tail -f /dev/null
