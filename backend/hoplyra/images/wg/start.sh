#!/bin/sh

CONF=${WG_CONF:-/etc/wireguard/wg0.conf}
[ -f "$CONF" ] || { echo "missing $CONF"; exit 1; }

# Never tear down an existing host interface: it may belong to another instance.
# A collision must fail closed rather than replacing somebody else's tunnel.
wg-quick up "$CONF" || { echo "wg-quick up failed"; exit 1; }
# Install teardown only after this process successfully created the interface.
# Normal compose stop/restart releases our own resources; SIGKILL remains
# fail-closed and requires operator cleanup rather than deleting an unknown link.
cleanup() {
    trap - TERM INT
    wg-quick down "$CONF" 2>/dev/null || true
    exit 0
}
trap cleanup TERM INT
tail -f /dev/null &
wait $!
