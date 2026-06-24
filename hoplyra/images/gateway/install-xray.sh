#!/bin/sh
set -e

ARCH=$(uname -m)
case "$ARCH" in
  x86_64) X=64 ;;
  aarch64) X=arm64-v8a ;;
  *) X=64 ;;
esac

VER=25.6.8
URL="https://github.com/XTLS/Xray-core/releases/download/v${VER}/Xray-linux-${X}.zip"

curl -fsSL -o /tmp/xray.zip "$URL"
unzip -o /tmp/xray.zip -d /tmp/xray
install -m 755 /tmp/xray/xray /usr/local/bin/xray
rm -rf /tmp/xray /tmp/xray.zip
