#!/bin/sh
set -eu
case "$(uname -m)" in
  x86_64) arch=amd64 ;;
  aarch64) arch=arm64 ;;
  *) echo "Unsupported Hysteria architecture: $(uname -m)" >&2; exit 1 ;;
esac
# Pin the official release for reproducible image builds.
version=v1.1.0
case "$arch" in
  amd64) expected_sha256="86da76ca130118acb5333ceffe51dcb27cb7d9349023a84dd9bb340cbd213a35" ;;
  arm64) expected_sha256="fd63fad1be8d5be6426b48f817dc56ae4058a95d3a88f298dfc4b5faa8a1fa47" ;;
esac

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT HUP INT TERM
curl --fail --location --retry 5 --retry-delay 2 --connect-timeout 30 \
  --output "$tmp" "https://github.com/HyNetworks/hysteria-realm-server/releases/download/${version}/hysteria-realm-server-linux-${arch}"
[ -s "$tmp" ]

echo "${expected_sha256}  ${tmp}" | sha256sum -c -
install -m 755 "$tmp" /usr/local/bin/hysteria-realm-server
/usr/local/bin/hysteria-realm-server --help || true
