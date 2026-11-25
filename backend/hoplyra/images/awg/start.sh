#!/bin/sh
set -eu

CONF=${AWG_CONF:-/etc/amnezia/amneziawg/awg0.conf}
[ -f "$CONF" ] || { echo "missing $CONF" >&2; exit 1; }

# Ensure tun device node is present
mkdir -p /dev/net
if [ ! -c /dev/net/tun ]; then
    mknod /dev/net/tun c 10 200 2>/dev/null || true
    chmod 600 /dev/net/tun 2>/dev/null || true
fi

# awg-quick uses amneziawg-go userspace binary when amneziawg kernel module is not present
awg-quick up "$CONF" || { echo "awg-quick up failed" >&2; exit 1; }

cleanup() {
    trap - TERM INT
    awg-quick down "$CONF" 2>/dev/null || true
    exit 0
}
trap cleanup TERM INT

tail -f /dev/null &
wait $!
