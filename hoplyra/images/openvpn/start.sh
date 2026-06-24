#!/bin/bash
set -euo pipefail

OPENVPN_DIR=/opt/hoplyra/openvpn
SUBNET="${OPENVPN_SUBNET:-10.8.0.0/24}"

mkdir -p /dev/net
if [ ! -c /dev/net/tun ]; then
  mknod /dev/net/tun c 10 200
fi

killall openvpn 2>/dev/null || true

if [ -f "$OPENVPN_DIR/client-chain.conf" ]; then
  if grep -q '^socks-proxy ' "$OPENVPN_DIR/client-chain.conf" 2>/dev/null; then
    for _ in $(seq 1 90); do
      if nc -z 127.0.0.1 9050 2>/dev/null; then
        break
      fi
      sleep 1
    done
  fi
  openvpn --config "$OPENVPN_DIR/client-chain.conf" --daemon
elif [ -f "$OPENVPN_DIR/ca.crt" ] && [ -f "$OPENVPN_DIR/server.conf" ]; then
  WAN=$(ip -4 route show default 2>/dev/null | awk '/default/ {print $5; exit}')
  if [ -n "$WAN" ] && [ -z "${OPENVPN_SKIP_WAN_NAT:-}" ]; then
    iptables -t nat -C POSTROUTING -s "$SUBNET" -o "$WAN" -j MASQUERADE 2>/dev/null ||
    iptables -t nat -A POSTROUTING -s "$SUBNET" -o "$WAN" -j MASQUERADE
  fi
  openvpn --config "$OPENVPN_DIR/server.conf" --daemon
fi

exec tail -f /dev/null
