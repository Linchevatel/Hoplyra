#!/bin/sh

CONF=/etc/wireguard/wg0.conf
[ -f "$CONF" ] || { echo "missing $CONF"; exit 1; }

wg-quick down "$CONF" 2>/dev/null || true
ip link del wg0 2>/dev/null || true
wg-quick up "$CONF" || { echo "wg-quick up failed"; exit 1; }
exec tail -f /dev/null
