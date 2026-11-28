#!/bin/sh
set -eu
exec shadowquic -c /etc/shadowquic/config.yaml
