"""Multi-peer management for standalone AWG, WireGuard, Xray, and OpenVPN configs."""
from __future__ import annotations

import ipaddress
import itertools
import json
import logging
import re
import shlex
import uuid
from datetime import datetime, timezone
from typing import Any

from hoplyra.db import connect, new_id
from hoplyra.remote import RemoteRunner
from hoplyra.wg_keys import generate_wg_keypair, generate_wg_psk

log = logging.getLogger("hoplyra.peers")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_subnet(cfg: dict[str, Any]) -> str:
    meta = json.loads(cfg.get("meta_json") or "{}")
    if meta.get("subnet"):
        return str(meta["subnet"])
    cc = cfg.get("client_config") or ""
    m = re.search(r"^Address\s*=\s*([0-9.]+)", cc, re.MULTILINE)
    if m:
        octets = m.group(1).split(".")
        if len(octets) == 4:
            return f"{octets[0]}.{octets[1]}.{octets[2]}.0/24"
    return "10.9.1.0/24"


def allocate_peer_ip(subnet_cidr: str, used_ips: set[str]) -> str:
    net = ipaddress.ip_network(subnet_cidr, strict=False)
    for host in itertools.islice(net.hosts(), 2, None):
        ip_str = str(host)
        if ip_str not in used_ips:
            return ip_str
    raise RuntimeError("В подсети закончились свободные IP-адреса для новых пиров")


def build_peer_client_config(
    base_client_config: str,
    private_key: str,
    preshared_key: str,
    client_ip: str,
) -> str:
    lines: list[str] = []
    has_psk = False
    for line in base_client_config.splitlines():
        if re.match(r"^\s*PrivateKey\s*=", line):
            lines.append(f"PrivateKey = {private_key}")
        elif re.match(r"^\s*Address\s*=", line):
            lines.append(f"Address = {client_ip}/32")
        elif re.match(r"^\s*PresharedKey\s*=", line):
            lines.append(f"PresharedKey = {preshared_key}")
            has_psk = True
        else:
            lines.append(line)
    if not has_psk:
        lines.append(f"PresharedKey = {preshared_key}")
    return "\n".join(lines) + "\n"


def _iface_and_prefix(cfg: dict[str, Any]) -> tuple[str, str, str]:
    meta = json.loads(cfg.get("meta_json") or "{}")
    protocol = cfg["protocol"]
    quote = shlex.quote
    iface = meta.get("interface") or f"{'awg' if protocol == 'awg' else 'wg'}_{cfg['id'][:8]}"
    if protocol == "wg":
        container = cfg.get("container_name") or f"cv-wg-{cfg['id'][:8]}"
        return iface, f"podman exec {quote(container)} wg", quote(iface)
    return iface, "awg", quote(iface)


def add_peer_on_host(
    runner: RemoteRunner,
    cfg: dict[str, Any],
    public_key: str,
    preshared_key: str,
    client_ip: str,
) -> None:
    protocol = cfg["protocol"]
    quote = shlex.quote
    _, tool_prefix, q_iface = _iface_and_prefix(cfg)
    allowed = quote(f"{client_ip}/32")
    if protocol == "wg":
        container = cfg.get("container_name") or f"cv-wg-{cfg['id'][:8]}"
        q_cont = quote(container)
        inner = (
            f"echo {quote(preshared_key)} > /tmp/psk.tmp && "
            f"wg set {q_iface} peer {quote(public_key)} preshared-key /tmp/psk.tmp allowed-ips {allowed}; "
            "rc=$?; rm -f /tmp/psk.tmp; exit $rc"
        )
        cmd = f"podman exec {q_cont} /bin/sh -c {quote(inner)}"
    else:
        inner_cmd = (
            f"{tool_prefix} set {q_iface} peer {quote(public_key)} "
            f"preshared-key <(printf '%s\\n' {quote(preshared_key)}) "
            f"allowed-ips {allowed}"
        )
        cmd = f"bash -lc {quote(inner_cmd)}"
    code, _, err = runner.run(cmd, timeout=30)
    if code != 0:
        raise RuntimeError(f"Не удалось добавить пира на сервер: {err[:300]}")
    inst = cfg.get("instance_path") or f"/opt/hoplyra/instances/{cfg['id']}"
    iface = _iface_and_prefix(cfg)[0]
    conf_file = f"{inst}/{iface}.conf"
    peer_block = f"\n[Peer]\nPublicKey = {public_key}\nPresharedKey = {preshared_key}\nAllowedIPs = {client_ip}/32\n"
    py_append = (
        f"p={conf_file!r}; "
        "import os; "
        "os.path.isfile(p) and ("
        "s := open(p).read(), "
        f"{public_key!r} not in s and open(p, 'a').write({peer_block!r})"
        ")"
    )
    runner.run(f"python3 -c {quote(py_append)}", timeout=15)


def remove_peer_on_host(
    runner: RemoteRunner,
    cfg: dict[str, Any],
    public_key: str,
) -> None:
    quote = shlex.quote
    _, tool_prefix, q_iface = _iface_and_prefix(cfg)
    cmd = f"{tool_prefix} set {q_iface} peer {quote(public_key)} remove"
    code, _, err = runner.run(cmd, timeout=30)
    if code != 0:
        raise RuntimeError(f"Не удалось удалить пира с сервера: {err[:300]}")
    inst = cfg.get("instance_path") or f"/opt/hoplyra/instances/{cfg['id']}"
    iface = _iface_and_prefix(cfg)[0]
    conf_file = f"{inst}/{iface}.conf"
    py_remove = (
        f"p={conf_file!r}; import os, re; "
        "os.path.isfile(p) and ("
        "s := open(p).read(), "
        f"open(p, 'w').write(re.sub(r'\\n?\\[Peer\\][^\\[]*?PublicKey\\s*=\\s*' + re.escape({public_key!r}) + r'[^\\[]*', '', s, flags=re.DOTALL))"
        ")"
    )
    runner.run(f"python3 -c {quote(py_remove)}", timeout=15)


def _create_xray_peer(cfg: dict[str, Any], runner: RemoteRunner, new_uuid: str) -> tuple[str, str]:
    quote = shlex.quote
    inst_path = cfg.get("instance_path") or f"/opt/hoplyra/instances/{cfg['id']}"
    container = cfg.get("container_name") or f"cv-xray-{cfg['id'][:8]}"
    conf_file = f"{inst_path}/config.json"

    py_add = (
        "import json; "
        f"p={conf_file!r}; "
        "d=json.load(open(p)); "
        "cl=d['inbounds'][0]['settings']['clients']; "
        "flow=cl[0].get('flow','') if cl else ''; "
        f"entry={{'id': {new_uuid!r}}} | ({{'flow': flow}} if flow else {{}}); "
        "cl.append(entry); "
        "json.dump(d, open(p,'w'), indent=2)"
    )
    code, _, err = runner.run(f"python3 -c {quote(py_add)} && podman restart {quote(container)}", timeout=45)
    if code != 0:
        raise RuntimeError(f"Не удалось добавить Xray-клиента: {err[:300]}")

    base_cc = cfg.get("client_config") or ""
    meta = json.loads(cfg.get("meta_json") or "{}")
    old_uuid = ""
    m = re.search(r"vless://([0-9a-fA-F-]{36})@", meta.get("vlessUri") or base_cc)
    if m:
        old_uuid = m.group(1)
    else:
        m2 = re.search(r'"id"\s*:\s*"([0-9a-fA-F-]{36})"', base_cc)
        if m2:
            old_uuid = m2.group(1)
    client_conf = base_cc.replace(old_uuid, new_uuid) if old_uuid else base_cc
    return new_uuid, client_conf


def _remove_xray_peer(cfg: dict[str, Any], runner: RemoteRunner, peer_uuid: str) -> None:
    quote = shlex.quote
    inst_path = cfg.get("instance_path") or f"/opt/hoplyra/instances/{cfg['id']}"
    container = cfg.get("container_name") or f"cv-xray-{cfg['id'][:8]}"
    conf_file = f"{inst_path}/config.json"
    py_del = (
        "import json; "
        f"p={conf_file!r}; "
        "d=json.load(open(p)); "
        f"d['inbounds'][0]['settings']['clients']=[c for c in d['inbounds'][0]['settings']['clients'] if c.get('id')!={peer_uuid!r}]; "
        "json.dump(d, open(p,'w'), indent=2)"
    )
    code, _, err = runner.run(f"python3 -c {quote(py_del)} && podman restart {quote(container)}", timeout=45)
    if code != 0:
        raise RuntimeError(f"Не удалось удалить Xray-клиента: {err[:300]}")


def _create_openvpn_peer(cfg: dict[str, Any], runner: RemoteRunner, cn: str) -> tuple[str, str]:
    quote = shlex.quote
    container = cfg.get("container_name") or f"cv-openvpn-{cfg['id'][:8]}"
    easyrsa = "$(command -v easyrsa || echo /usr/share/easy-rsa/easyrsa)"
    inner = (
        f"cd /opt/hoplyra/openvpn && EASYRSA_BATCH=1 {easyrsa} build-client-full {quote(cn)} nopass >/dev/null && "
        f"echo '---CERT---' && cat pki/issued/{quote(cn)}.crt && "
        f"echo '---KEY---' && cat pki/private/{quote(cn)}.key"
    )
    code, out, err = runner.run(f"podman exec {quote(container)} sh -lc {quote(inner)}", timeout=60)
    if code != 0 or "---CERT---" not in out or "---KEY---" not in out:
        raise RuntimeError(f"Не удалось выпустить OpenVPN-сертификат: {err[:300]}")
    cert_part = out.split("---CERT---", 1)[1].split("---KEY---", 1)[0].strip()
    key_part = out.split("---KEY---", 1)[1].strip()
    base_cc = cfg.get("client_config") or ""
    client_conf = re.sub(r"<cert>.*?</cert>", f"<cert>\n{cert_part}\n</cert>", base_cc, flags=re.DOTALL)
    client_conf = re.sub(r"<key>.*?</key>", f"<key>\n{key_part}\n</key>", client_conf, flags=re.DOTALL)
    return cn, client_conf


def _remove_openvpn_peer(cfg: dict[str, Any], runner: RemoteRunner, cn: str) -> None:
    quote = shlex.quote
    container = cfg.get("container_name") or f"cv-openvpn-{cfg['id'][:8]}"
    easyrsa = "$(command -v easyrsa || echo /usr/share/easy-rsa/easyrsa)"
    inner = (
        f"cd /opt/hoplyra/openvpn && EASYRSA_BATCH=1 {easyrsa} revoke {quote(cn)} >/dev/null && "
        f"EASYRSA_BATCH=1 {easyrsa} gen-crl >/dev/null && "
        "chmod 755 pki && chmod 644 pki/crl.pem && "
        "(grep -q '^crl-verify ' server.conf || echo 'crl-verify /opt/hoplyra/openvpn/pki/crl.pem' >> server.conf)"
    )
    code, _, err = runner.run(
        f"podman exec {quote(container)} sh -lc {quote(inner)} && podman restart {quote(container)}",
        timeout=45,
    )
    if code != 0:
        raise RuntimeError(f"Не удалось отозвать OpenVPN-сертификат: {err[:300]}")


def _build_peer_amnezia_uri(cfg: dict[str, Any], peer_name: str, client_conf: str) -> str | None:
    if cfg.get("protocol") != "awg":
        return None
    try:
        from hoplyra.amnezia_export import build_amnezia_awg_vpn_uri

        meta = json.loads(cfg.get("meta_json") or "{}")
        m_ep = re.search(r"^Endpoint\s*=\s*(\[[^\]]+\]|[^:\s]+):(\d+)", client_conf, re.MULTILINE)
        host = m_ep.group(1).strip("[]") if m_ep else ""
        port = int(m_ep.group(2)) if m_ep else int(meta.get("listenPort") or 55424)
        return build_amnezia_awg_vpn_uri(
            client_conf,
            host=host,
            port=port,
            description=peer_name,
            awg_version=str(meta.get("awgVersion") or "awg3.1"),
        )
    except Exception:
        return None


def get_peer_stats(cfg: dict[str, Any], runner: RemoteRunner) -> dict[str, dict[str, Any]]:
    """Retrieve real-time rx/tx bytes and last_handshake for all peers on server.
    
    Returns mapping: public_key (or peer identifier) -> {
      "rxBytes": int,
      "txBytes": int,
      "lastHandshake": int (epoch timestamp or 0)
    }
    """
    protocol = cfg["protocol"]
    if protocol not in ("awg", "wg", "xray", "openvpn"):
        return {}

    stats: dict[str, dict[str, Any]] = {}
    if protocol in ("awg", "wg"):
        _, tool_prefix, q_iface = _iface_and_prefix(cfg)
        cmd = f"{tool_prefix} show {q_iface} dump"
        code, out, _ = runner.run(cmd, timeout=10)
        if code == 0:
            for line in out.splitlines():
                parts = line.strip().split("\t")
                # Format wg dump:
                # interface: private-key public-key listen-port fwmark
                # peer: public-key preshared-key endpoint allowed-ips latest-handshake transfer-rx transfer-tx persistent-keepalive
                if len(parts) >= 8:
                    pub = parts[0]
                    try:
                        handshake = int(parts[4])
                        rx = int(parts[5])
                        tx = int(parts[6])
                        stats[pub] = {
                            "rxBytes": rx,
                            "txBytes": tx,
                            "lastHandshake": handshake,
                        }
                    except ValueError:
                        continue
    elif protocol == "openvpn":
        inst = cfg.get("instance_path") or f"/opt/hoplyra/instances/{cfg['id']}"
        status_file = f"{inst}/status.log"
        quote = shlex.quote
        code, out, _ = runner.run(f"cat {quote(status_file)} 2>/dev/null", timeout=10)
        if code == 0:
            # OpenVPN status.log CLIENT LIST
            for line in out.splitlines():
                parts = line.split(",")
                if len(parts) >= 5 and parts[0] != "Common Name":
                    cn = parts[0]
                    try:
                        rx = int(parts[2])
                        tx = int(parts[3])
                        # Для OpenVPN handshake не применим, но передаем признак активного подключения
                        stats[cn] = {
                            "rxBytes": rx,
                            "txBytes": tx,
                            "lastHandshake": None,
                            "isConnected": True,
                        }
                    except ValueError:
                        continue
    return stats


def _extract_xray_peer_vless_uri(cfg: dict[str, Any], peer_uuid: str) -> str | None:
    if cfg.get("protocol") != "xray":
        return None
    meta = json.loads(cfg.get("meta_json") or "{}")
    base_uri = meta.get("vlessUri") or ""
    if not base_uri:
        return None
    old_uuid = ""
    m = re.search(r"vless://([0-9a-fA-F-]{36})@", base_uri)
    if m:
        old_uuid = m.group(1)
        return base_uri.replace(old_uuid, peer_uuid)
    return None


def list_peers(config_id: str, runner: RemoteRunner | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        cfg_row = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
        cfg = dict(cfg_row) if cfg_row else {}
        rows = conn.execute(
            "SELECT id, config_id, name, public_key, client_ip, client_config, created_at "
            "FROM config_peers WHERE config_id = ? AND revoked_at IS NULL ORDER BY created_at ASC",
            (config_id,),
        ).fetchall()

    stats: dict[str, dict[str, Any]] = {}
    if runner and cfg.get("status") == "active":
        try:
            stats = get_peer_stats(cfg, runner)
        except Exception as exc:
            log.warning("Failed retrieving peer live stats for %s: %s", config_id, exc)

    res = []
    protocol = cfg.get("protocol")
    for r in rows:
        pub = r["public_key"]
        p_stat = stats.get(pub)
        # Замечание [2]: Для протоколов без статистики (Xray) или когда сбор не дал данных возвращаем None, а не ложный 0
        has_stats = p_stat is not None
        res.append({
            "id": r["id"],
            "configId": r["config_id"],
            "name": r["name"],
            "publicKey": pub,
            "clientIp": r["client_ip"],
            "clientConfig": r["client_config"],
            "amneziaVpnUri": _build_peer_amnezia_uri(cfg, r["name"], r["client_config"]),
            "vlessUri": _extract_xray_peer_vless_uri(cfg, pub),
            "createdAt": r["created_at"],
            "rxBytes": p_stat.get("rxBytes") if has_stats else None,
            "txBytes": p_stat.get("txBytes") if has_stats else None,
            "lastHandshake": p_stat.get("lastHandshake") if has_stats else None,
            "isConnected": p_stat.get("isConnected") if has_stats else None,
            "statsSupported": protocol in ("awg", "wg", "openvpn"),
        })
    return res


def create_peer(config_id: str, name: str, runner: RemoteRunner) -> dict[str, Any]:
    with connect() as conn:
        cfg_row = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
        if not cfg_row:
            raise ValueError("Config not found")
        cfg = dict(cfg_row)
        meta = json.loads(cfg.get("meta_json") or "{}")
        if meta.get("chain") or meta.get("hops"):
            raise ValueError("Multi-peer не поддерживается для цепей")
        if cfg["status"] != "active":
            raise RuntimeError("Конфигурация не активна")
        protocol = cfg["protocol"]
        if protocol not in ("awg", "wg", "xray", "openvpn"):
            raise ValueError("Multi-peer не поддерживается для этого протокола")
        used = {
            r["client_ip"]
            for r in conn.execute(
                "SELECT client_ip FROM config_peers WHERE config_id = ? AND revoked_at IS NULL",
                (config_id,),
            ).fetchall()
        }

    peer_id = new_id()
    now = _now_iso()
    if protocol in ("awg", "wg"):
        subnet = _extract_subnet(cfg)
        client_ip = allocate_peer_ip(subnet, used)
        priv_key, pub_key = generate_wg_keypair()
        psk = generate_wg_psk()
        client_conf = build_peer_client_config(cfg.get("client_config") or "", priv_key, psk, client_ip)
        add_peer_on_host(runner, cfg, pub_key, psk, client_ip)
    elif protocol == "xray":
        pub_key, client_conf = _create_xray_peer(cfg, runner, str(uuid.uuid4()))
        psk = ""
        client_ip = f"UUID {pub_key[:8]}"
    else:
        pub_key, client_conf = _create_openvpn_peer(cfg, runner, f"peer_{peer_id[:8]}")
        psk = ""
        client_ip = pub_key

    with connect() as conn:
        conn.execute(
            "INSERT INTO config_peers (id, config_id, name, public_key, preshared_key, client_ip, client_config, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (peer_id, config_id, name.strip(), pub_key, psk, client_ip, client_conf, now),
        )
        conn.commit()
    return {
        "id": peer_id,
        "configId": config_id,
        "name": name.strip(),
        "publicKey": pub_key,
        "clientIp": client_ip,
        "clientConfig": client_conf,
        "amneziaVpnUri": _build_peer_amnezia_uri(cfg, name.strip(), client_conf),
        "vlessUri": _extract_xray_peer_vless_uri(cfg, pub_key),
        "createdAt": now,
    }


def revoke_peer(config_id: str, peer_id: str, runner: RemoteRunner) -> None:
    with connect() as conn:
        cfg_row = conn.execute("SELECT * FROM configs WHERE id = ?", (config_id,)).fetchone()
        peer_row = conn.execute(
            "SELECT * FROM config_peers WHERE id = ? AND config_id = ? AND revoked_at IS NULL",
            (peer_id, config_id),
        ).fetchone()
    if not cfg_row or not peer_row:
        raise ValueError("Peer not found")
    cfg = dict(cfg_row)
    meta = json.loads(cfg.get("meta_json") or "{}")
    if meta.get("chain") or meta.get("hops"):
        raise ValueError("Multi-peer не поддерживается для цепей")
    protocol = cfg["protocol"]
    if protocol in ("awg", "wg"):
        remove_peer_on_host(runner, cfg, peer_row["public_key"])
    elif protocol == "xray":
        _remove_xray_peer(cfg, runner, peer_row["public_key"])
    elif protocol == "openvpn":
        _remove_openvpn_peer(cfg, runner, peer_row["public_key"])
    with connect() as conn:
        conn.execute(
            "UPDATE config_peers SET revoked_at = ? WHERE id = ?",
            (_now_iso(), peer_id),
        )
        conn.commit()
