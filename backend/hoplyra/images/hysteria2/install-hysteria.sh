#!/bin/sh
set -eu
case "$(uname -m)" in
  x86_64) arch=amd64 ;;
  aarch64) arch=arm64 ;;
  *) echo "Unsupported Hysteria architecture: $(uname -m)" >&2; exit 1 ;;
esac
# Pin the official release for reproducible image builds.
version=v2.14.0
case "$arch" in
  amd64) expected_sha256="fc25edff9948708cb00a4ff01f3bccde677660aca050133bb1ad46c6860362d2" ;;
  arm64) expected_sha256="e7c52241299fe85aba11e9d36e85d8d88ddbb5799ac0e33d272219d20e313bab" ;;
esac

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT HUP INT TERM
curl --fail --location --retry 5 --retry-delay 2 --connect-timeout 30 \
  --output "$tmp" "https://github.com/apernet/hysteria/releases/download/app/${version}/hysteria-linux-${arch}"
[ -s "$tmp" ]

echo "${expected_sha256}  ${tmp}" | sha256sum -c -
install -m 755 "$tmp" /usr/local/bin/hysteria
hysteria version
