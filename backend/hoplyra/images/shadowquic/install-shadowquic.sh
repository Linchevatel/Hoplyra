#!/bin/sh
set -eu
case "$(uname -m)" in
  x86_64) arch=x86_64 ;;
  aarch64) arch=aarch64 ;;
  *) echo "Unsupported ShadowQUIC architecture: $(uname -m)" >&2; exit 1 ;;
esac
# Pin the official release for reproducible image builds.
version=v0.4.14
case "$arch" in
  x86_64) expected_sha256="bfb27d420276116017c280b0caaa7d9e6b4e87cd50d77266b970af9b5a3677c9" ;;
  aarch64) expected_sha256="ebe041c61ce74ac80a09ec3d05aea12a929ab201a78b5a9e7763ec86f4a79f2b" ;;
esac

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT HUP INT TERM
curl --fail --location --retry 5 --retry-delay 2 --connect-timeout 30 \
  --output "$tmp" "https://github.com/spongebob888/shadowquic/releases/download/${version}/shadowquic-${arch}-linux-musl"
[ -s "$tmp" ]

echo "${expected_sha256}  ${tmp}" | sha256sum -c -
install -m 755 "$tmp" /usr/local/bin/shadowquic
shadowquic --version
