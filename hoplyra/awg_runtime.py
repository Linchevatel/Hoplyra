
from __future__ import annotations

import re
from typing import Any

from hoplyra.remote import RemoteRunner, nat_postdown, nat_postup
from hoplyra.awg_params import (
    AWG2_DEFAULT_I1,
    AwgObfuscationParams,
    ensure_awg2_obfuscation_in_conf,
    generate_awg2_params,
    parse_awg_params_from_conf,
)
from hoplyra.amnezia_export import build_amnezia_awg_vpn_uri
from hoplyra.wg_keys import generate_wg_psk

AWG_READY_MARKER = "/opt/hoplyra/.awg-ready"
CHAINVAULT_AWG_MARKER = "/opt/chainvault/.awg-ready"

HOST_AWG_INSTALL = r"""#!/bin/bash
set -euo pipefail
if [ -f /opt/hoplyra/.awg-ready ] && command -v awg-quick >/dev/null 2>&1; then
  modprobe amneziawg 2>/dev/null || true
  exit 0
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq software-properties-common gnupg curl iproute2 iptables
if ! apt-get install -y -qq amneziawg amneziawg-tools 2>/dev/null; then
  add-apt-repository -y ppa:amnezia/ppa
  apt-get update -qq
  apt-get install -y -qq amneziawg amneziawg-tools iproute2 iptables
fi
modprobe amneziawg
date -Iseconds > /opt/hoplyra/.awg-ready
"""


def _parse_kv_conf(conf: str) -> dict[str, str]:
    kv: dict[str, str] = {}
    for line in conf.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if " = " not in line:
            continue
        key, value = line.split(" = ", 1)
        kv[key.strip()] = value.strip()
    return kv


def _ensure_peer_psk(conf: str, psk: str) -> str:
    if re.search(r"^PresharedKey\s*=", conf, re.MULTILINE):
        return conf
    return re.sub(
        r"(^\[Peer\]\nPublicKey = .+\n)",
        rf"\1PresharedKey = {psk}\n",
        conf,
        count=1,
        flags=re.MULTILINE,
    )


def ensure_amnezia_awg_headers(
    conf: str,
    params: AwgObfuscationParams | None = None,
) -> str:
    awg = params or generate_awg2_params()
    if parse_awg_params_from_conf(conf) and re.search(r"^I1\s*=", conf, re.MULTILINE):
        return conf
    return ensure_awg2_obfuscation_in_conf(conf, awg)


def refresh_awg_server_nat(runner: RemoteRunner, conf_path: str, subnet: str = "10.9.1.0/24") -> None:
    _, conf, err = runner.run(f"cat {conf_path}", timeout=30)
    if not conf.strip():
        raise RuntimeError(f"Не найден {conf_path}: {err[:200]}")

    post_up = nat_postup(subnet)
    post_down = nat_postdown(subnet)
    updated = conf
    if "PostUp =" in updated:
        updated = re.sub(r"^PostUp = .*$", f"PostUp = {post_up}", updated, count=1, flags=re.MULTILINE)
        updated = re.sub(r"^PostDown = .*$", f"PostDown = {post_down}", updated, count=1, flags=re.MULTILINE)
    else:
        updated = updated.replace(
            "\n[Peer]",
            f"\nPostUp = {post_up}\nPostDown = {post_down}\n\n[Peer]",
            1,
        )

    if updated != conf:
        runner.upload_text(conf_path, updated, 0o600)

    awg_quick_down(runner, conf_path)
    awg_quick_up(runner, conf_path)


def repair_awg_for_amnezia(
    runner: RemoteRunner,
    instance_path: str,
    client_conf: str,
    *,
    host: str,
    port: int = 55424,
    description: str,
) -> dict[str, Any]:
    from hoplyra.awg_params import build_awg_client_conf

    server_path = f"{instance_path}/awg0.conf"
    _, server_conf, err = runner.run(f"cat {server_path}", timeout=30)
    if not server_conf.strip():
        raise RuntimeError(f"Не найден {server_path}: {err[:200]}")

    server_params = parse_awg_params_from_conf(server_conf) or generate_awg2_params()
    if not server_params.i1.strip():
        server_params = AwgObfuscationParams(
            jc=server_params.jc,
            jmin=server_params.jmin,
            jmax=server_params.jmax,
            s1=server_params.s1,
            s2=server_params.s2,
            s3=server_params.s3,
            s4=server_params.s4,
            h1=server_params.h1,
            h2=server_params.h2,
            h3=server_params.h3,
            h4=server_params.h4,
            i1=AWG2_DEFAULT_I1,
            i2=server_params.i2,
            i3=server_params.i3,
            i4=server_params.i4,
            i5=server_params.i5,
        )

    server_kv = _parse_kv_conf(server_conf)
    client_kv = _parse_kv_conf(client_conf)
    psk = (
        server_kv.get("PresharedKey")
        or client_kv.get("PresharedKey")
        or generate_wg_psk()
    )

    new_server = ensure_awg2_obfuscation_in_conf(server_conf, server_params)
    new_server = _ensure_peer_psk(new_server, psk)

    new_client = build_awg_client_conf(
        client_priv=client_kv["PrivateKey"],
        server_pub=client_kv["PublicKey"],
        server_host=host,
        listen_port=port,
        client_ip=client_kv.get("Address", "10.9.1.2/32"),
        dns=client_kv.get("DNS", "1.1.1.1, 8.8.8.8"),
        params=server_params,
        preshared_key=psk,
    )

    if new_server != server_conf:
        runner.upload_text(server_path, new_server, 0o600)

    refresh_awg_server_nat(runner, server_path)

    return {
        "clientConfig": new_client,
        "amneziaVpnUri": build_amnezia_awg_vpn_uri(
            new_client,
            host=host,
            port=port,
            description=description,
        ),
        "awgParams": server_params.as_meta(),
        "awgVersion": 2,
    }


def ensure_awg_on_host(runner: RemoteRunner) -> None:
    if runner.target.is_local:
        return
    runner.run(f"bash -s << 'EOF'\n{HOST_AWG_INSTALL}\nEOF", timeout=1200)
    code, _, err = runner.run("command -v awg-quick >/dev/null && modprobe amneziawg && echo ok")
    if code != 0:
        raise RuntimeError(f"amneziawg on host failed: {err[:500]}")


def awg_quick_up(runner: RemoteRunner, conf_path: str) -> None:
    runner.run(
        f"modprobe amneziawg 2>/dev/null || true; "
        f"awg-quick down {conf_path} 2>/dev/null || true; "
        f"awg-quick up {conf_path}",
        timeout=120,
    )


def awg_quick_down(runner: RemoteRunner, conf_path: str) -> None:
    runner.run(f"awg-quick down {conf_path} 2>/dev/null || true", timeout=60)


def chain_tag_from_config_id(config_id: str) -> str:
    tail = config_id.rsplit("-", 1)[-1]
    return tail[:8] if len(tail) >= 8 else config_id.replace("-", "")[:8]


HOST_AWG_UPGRADE_APT = r"""#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq software-properties-common gnupg curl iproute2 iptables 2>/dev/null || true
if ! apt-cache show amneziawg &>/dev/null 2>&1; then
  add-apt-repository -y ppa:amnezia/ppa
  apt-get update -qq
fi
apt-get install -y -qq amneziawg amneziawg-tools
apt-get install -y -qq --only-upgrade amneziawg amneziawg-tools 2>/dev/null || true
"""


def discover_awg_confs(runner: RemoteRunner, config_id: str | None = None) -> list[str]:
    if config_id:
        roots = (
            f"/opt/hoplyra/chains/{config_id}",
            f"/opt/hoplyra/instances/{config_id}",
            f"/opt/chainvault/chains/{config_id}",
            f"/opt/chainvault/instances/{config_id}",
        )
        find_roots = " ".join(roots)
    else:
        find_roots = (
            "/opt/hoplyra/chains /opt/hoplyra/instances "
            "/opt/chainvault/chains /opt/chainvault/instances"
        )
    code, out, _ = runner.run(
        f"find {find_roots} -name 'awg*.conf' 2>/dev/null | sort -u",
        timeout=60,
    )
    if code != 0 and not out.strip():
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _parse_pkg_versions(raw: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in raw.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            versions[parts[0]] = parts[1]
    return versions


def upgrade_awg_on_host(
    runner: RemoteRunner,
    *,
    config_id: str | None = None,
    chain_tag: str | None = None,
    instance_only: bool = False,
) -> dict[str, Any]:
    if runner.target.is_local:
        return {"skipped": True, "reason": "local"}

    code, out, err = runner.run(
        f"bash -s << 'EOF'\n{HOST_AWG_UPGRADE_APT}\nEOF",
        timeout=1200,
    )
    if code != 0:
        raise RuntimeError((err or out).strip()[:800] or "apt upgrade failed")

    confs = discover_awg_confs(runner, config_id)
    if instance_only and config_id:
        instance_root = f"/opt/hoplyra/instances/{config_id}"
        confs = [conf for conf in confs if conf.startswith(instance_root)]

    for conf in confs:
        awg_quick_down(runner, conf)

    runner.run("modprobe -r amneziawg 2>/dev/null || true; modprobe amneziawg", timeout=60)

    for conf in confs:
        awg_quick_up(runner, conf)

    restarted: list[str] = []
    if chain_tag and not instance_only:
        _, names_out, _ = runner.run(
            f"podman ps --format '{{{{.Names}}}}' 2>/dev/null | grep -E 'cv-.+-{chain_tag}$' || true",
            timeout=30,
        )
        for name in names_out.splitlines():
            container = name.strip()
            if not container:
                continue
            rc, _, restart_err = runner.run(f"podman restart {container}", timeout=120)
            if rc == 0:
                restarted.append(container)
            elif restart_err.strip():
                raise RuntimeError(f"podman restart {container}: {restart_err.strip()[:300]}")

    _, pkg_out, _ = runner.run(
        "dpkg -l amneziawg amneziawg-tools 2>/dev/null | awk '/^ii/ {print $2\" \"$3}'",
        timeout=30,
    )
    _, awg_ver, _ = runner.run("awg --version 2>/dev/null | head -1", timeout=15)
    _, mod_ver, _ = runner.run(
        "modinfo amneziawg 2>/dev/null | grep '^version:' | awk '{print $2}'",
        timeout=15,
    )
    runner.run(
        f"date -Iseconds > {AWG_READY_MARKER}; "
        f"mkdir -p /opt/chainvault && date -Iseconds > {CHAINVAULT_AWG_MARKER}",
        timeout=15,
    )

    return {
        "packages": _parse_pkg_versions(pkg_out),
        "awgVersion": awg_ver.strip(),
        "moduleVersion": mod_ver.strip(),
        "confs": confs,
        "restartedContainers": restarted,
    }


def config_uses_awg(protocol: str, meta: dict[str, Any] | None) -> bool:
    meta = meta or {}
    if meta.get("chain"):
        return any(h.get("protocol") == "awg" for h in meta.get("hops", []))
    return protocol == "awg"


def awg_server_ids(protocol: str, server_id: str, meta: dict[str, Any] | None) -> list[str]:
    meta = meta or {}
    if meta.get("chain"):
        return list(
            dict.fromkeys(
                h["serverId"] for h in meta.get("hops", []) if h.get("protocol") == "awg"
            )
        )
    if protocol == "awg":
        return [server_id]
    return []
