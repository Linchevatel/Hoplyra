"""Shared host DNS preparation for wg-quick/awg-quick."""

HOST_DNS_SETUP = r"""
ensure_hoplyra_dns() {
  # Containers and non-systemd hosts cannot start resolved.
  if ! command -v systemctl >/dev/null 2>&1 || [ ! -d /run/systemd/system ]; then
    echo '[hoplyra-dns] non-systemd host: skipping resolved setup'
    return 0
  fi
  # Keep the original resolver usable during package installation and startup.
  local upstream original_link=
  if [ -L /etc/resolv.conf ] && [ -e /etc/resolv.conf ]; then
    original_link=$(readlink /etc/resolv.conf)
  fi
  upstream=$(awk '$1 == "nameserver" && $2 !~ /^127\./ && $2 != "::1" {print $2}' /etc/resolv.conf 2>/dev/null | sort -u | tr '\n' ' ' || true)
  if [ -e /etc/resolv.conf ] && [ ! -e /etc/resolv.conf.hoplyra-backup ]; then
    cp -pL /etc/resolv.conf /etc/resolv.conf.hoplyra-backup
  fi
  if ! systemctl cat systemd-resolved.service >/dev/null 2>&1; then
    if ! command -v apt-get >/dev/null 2>&1; then
      echo '[hoplyra-dns] no supported package manager: skipping resolved setup'
      return 0
    fi
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y systemd-resolved || apt-get install -y systemd
  fi
  mkdir -p /etc/systemd/resolved.conf.d
  if [ ! -f /etc/systemd/resolved.conf.d/60-hoplyra-dns.conf ]; then
    printf '[Resolve]\nDNS=%s\nFallbackDNS=1.1.1.1 8.8.8.8\n' "${upstream:-1.1.1.1 8.8.8.8}" > /etc/systemd/resolved.conf.d/60-hoplyra-dns.conf
  fi
  systemctl enable --now systemd-resolved
  systemctl restart systemd-resolved
  # Package postinst may replace resolv.conf: restore a pre-existing valid link.
  if [ -n "$original_link" ]; then
    ln -sfn "$original_link" /etc/resolv.conf
  fi
  # Preserve any existing valid symlink (NetworkManager, resolved, etc.).
  if ! { [ -L /etc/resolv.conf ] && [ -e /etc/resolv.conf ]; }; then
    [ -e /run/systemd/resolve/stub-resolv.conf ] || return 1
    ln -sfn /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
  fi
}
ensure_hoplyra_dns
"""
