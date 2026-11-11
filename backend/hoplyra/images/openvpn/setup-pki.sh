#!/bin/bash
set -euo pipefail

OPENVPN_DIR=/opt/hoplyra/openvpn
cd "$OPENVPN_DIR"
export EASYRSA_BATCH=1

rm -rf pki
rm -f ca.crt server.crt server.key client.crt client.key ta.key

easyrsa init-pki
easyrsa build-ca nopass
easyrsa build-server-full server nopass
easyrsa build-client-full client nopass
cp pki/ca.crt .
cp pki/issued/server.crt .
cp pki/private/server.key .
cp pki/issued/client.crt .
cp pki/private/client.key .
openvpn --genkey --secret ta.key
