"""Multi-peer management for standalone AWG, WireGuard, Xray, and OpenVPN configs."""
from __future__ import annotations

import ipaddress
import itertools
import json
import logging
import re
import shlex
import secrets
from urllib.parse import quote, urlsplit, urlunsplit
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
    container = cfg.get("container_name") or f"cv-{protocol}-{cfg['id'][:8]}"
    is_host_awg = meta.get("hostAwg", True if protocol == "awg" else False)
    if protocol == "wg":
        return iface, f"podman exec {quote(container)} wg", quote(iface)
    if protocol == "awg" and not is_host_awg:
        return iface, f"podman exec {quote(container)} awg", quote(iface)
    return iface, "awg", quote(iface)


def _container_tool_cmd(container: str, tool: str, subcmd: str) -> str:
    """Build a reliable podman/docker exec command with arguments inside the invocation."""
    quote = shlex.quote
    q_cont = quote(container)
    return (
        f"if command -v podman >/dev/null 2>&1; then "
        f"podman exec {q_cont} {tool} {subcmd}; "
        f"else docker exec {q_cont} {tool} {subcmd}; fi"
    )


def add_peer_on_host(
    runner: RemoteRunner,
    cfg: dict[str, Any],
    public_key: str,
    preshared_key: str,
    client_ip: str,
) -> None:
    protocol = cfg["protocol"]
    quote = shlex.quote
    meta = json.loads(cfg.get("meta_json") or "{}")
    is_host_awg = meta.get("hostAwg", True if protocol == "awg" else False)
    is_container = protocol == "wg" or (protocol == "awg" and not is_host_awg)
    tool = "wg" if protocol == "wg" else "awg"
    _, tool_prefix, q_iface = _iface_and_prefix(cfg)
    allowed = quote(f"{client_ip}/32")

    if is_container:
        container = cfg.get("container_name") or f"cv-{protocol}-{cfg['id'][:8]}"
        q_cont = quote(container)
        inner = (
            "t=$(mktemp /tmp/psk.XXXXXX) && chmod 0600 \"$t\" && "
            "trap 'rm -f \"$t\"' EXIT INT TERM && "
            f"printf '%s\\n' {quote(preshared_key)} > \"$t\" && "
            f"{tool} set {q_iface} peer {quote(public_key)} preshared-key \"$t\" allowed-ips {allowed}"
        )
        cmd = (
            f"if command -v podman >/dev/null 2>&1; then "
            f"podman exec {q_cont} /bin/sh -c {quote(inner)}; "
            f"else docker exec {q_cont} /bin/sh -c {quote(inner)}; fi"
        )
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
    protocol = cfg["protocol"]
    quote = shlex.quote
    meta = json.loads(cfg.get("meta_json") or "{}")
    is_host_awg = meta.get("hostAwg", True if protocol == "awg" else False)
    is_container = protocol == "wg" or (protocol == "awg" and not is_host_awg)
    tool = "wg" if protocol == "wg" else "awg"
    _, tool_prefix, q_iface = _iface_and_prefix(cfg)

    if is_container:
        container = cfg.get("container_name") or f"cv-{protocol}-{cfg['id'][:8]}"
        cmd = _container_tool_cmd(container, tool, f"set {q_iface} peer {quote(public_key)} remove")
    else:
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


def _hysteria2_client(base: str, auth: str, name: str) -> str:
    """Replace only client credentials and the URI fragment, preserving TLS settings."""
    match = re.search(r"hysteria2://[^\s]+", base)
    if not match:
        raise ValueError("Hysteria 2 client URI missing")
    uri = urlsplit(match.group())
    endpoint = uri.netloc.rsplit("@", 1)[-1]
    new_uri = urlunsplit((uri.scheme, f"{quote(auth, safe='')}@{endpoint}", uri.path, uri.query, quote(name, safe="")))
    client = base.replace(match.group(), new_uri)
    return re.sub(r"^auth:.*$", lambda _: f"auth: {json.dumps(auth)}", client, flags=re.MULTILINE)


def _update_hysteria2_auth(cfg: dict[str, Any], runner: RemoteRunner, username: str,
                          password: str, *, remove: bool = False) -> None:
    # The deployer owns this top-level YAML block. JSON is a YAML subset and
    # avoids requiring PyYAML on the remote host. Lock across read/write/restart.
    path = cfg.get("instance_path") or f"/opt/hoplyra/instances/{cfg['id']}"
    container = cfg.get("container_name") or f"cv-hy2-{cfg['id'][:8]}"
    script = rf"""
import fcntl, json, os, re, subprocess
p = {path + '/config.yaml'!r}
with open(p + '.lock', 'w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    original = open(p).read()
    match = re.search(r'^auth:.*?(?=^\S|\Z)', original, re.M | re.S)
    if not match:
        raise RuntimeError('auth block missing')
    block = match.group()
    if block.startswith('auth: {{'):
        auth = json.loads(block.split(':', 1)[1])
    else:
        kind = re.search(r'^  type: (\w+)', block, re.M)
        if kind and kind.group(1) == 'password':
            value = re.search(r'^  password: (.+)', block, re.M)
            auth = {{'type': 'userpass', 'userpass': {{'primary': json.loads(value.group(1))}}}}
        elif kind and kind.group(1) == 'userpass':
            users = re.findall(r'^    ([a-zA-Z0-9_]+): (.+)$', block, re.M)
            if not users:
                raise RuntimeError('unsupported userpass format')
            auth = {{'type': 'userpass', 'userpass': {{k: json.loads(v) for k, v in users}}}}
        else:
            raise RuntimeError('unsupported auth format')
    if auth.get('type') != 'userpass':
        raise RuntimeError('unsupported auth type')
    users = auth['userpass']
    username, password = {username!r}, {password!r}
    if {remove!r}:
        if users.get(username) != password:
            raise RuntimeError('peer credentials do not match')
        del users[username]
    else:
        if username in users:
            raise RuntimeError('peer already exists')
        users[username] = password
    updated = original[:match.start()] + 'auth: ' + json.dumps(auth) + '\n' + original[match.end():]
    def write(content):
        with open(p + '.new', 'w') as out:
            os.chmod(p + '.new', 0o600)
            out.write(content)
        os.replace(p + '.new', p)
    write(updated)
    def _restart():
        for bin_name in ('podman', 'docker'):
            try:
                res = subprocess.run([bin_name, 'restart', {container!r}], capture_output=True)
                if res.returncode == 0:
                    return 0
            except FileNotFoundError:
                continue
        return 1
    if _restart() != 0:
        write(original)
        _restart()
        raise RuntimeError('container restart failed')
"""
    code, out, err = runner.run(f"python3 -c {shlex.quote(script)}", timeout=60)
    if code != 0:
        cleaned_err = re.sub(r'password.*', '[REDACTED]', err or out, flags=re.I)
        cleaned_err = cleaned_err.replace(password, '[REDACTED]')
        raise RuntimeError(f"Не удалось обновить Hysteria 2-клиента: {cleaned_err}")


def _create_hysteria2_peer(cfg: dict[str, Any], runner: RemoteRunner, peer_name: str) -> tuple[str, str]:
    username = f"peer_{uuid.uuid4().hex}"
    password = secrets.token_urlsafe(24)
    client = _hysteria2_client(cfg.get("client_config") or "", f"{username}:{password}", peer_name)
    _update_hysteria2_auth(cfg, runner, username, password)
    return username, client


def _remove_hysteria2_peer(cfg: dict[str, Any], runner: RemoteRunner, peer_id: str, password: str) -> None:
    _update_hysteria2_auth(cfg, runner, peer_id, password, remove=True)


def _shadowquic_client(base: str, username: str, password: str, name: str) -> str:
    """Replace only client credentials and the URI fragment, preserving JLS settings."""
    match = re.search(r"(?:sq|shadowquic)://[^\s]+", base)
    if not match:
        raise ValueError("ShadowQUIC client URI missing")
    uri = urlsplit(match.group())
    endpoint = uri.netloc.rsplit("@", 1)[-1]
    userinfo = f"{quote(username, safe='')}:{quote(password, safe='')}"
    new_uri = urlunsplit((uri.scheme, f"{userinfo}@{endpoint}", uri.path, uri.query, quote(name, safe="")))
    client = base.replace(match.group(), new_uri)
    client = re.sub(r"^(\s*username:\s*).*$", lambda m: f"{m.group(1)}{json.dumps(username)}", client, flags=re.MULTILINE)
    return re.sub(r"^(\s*password:\s*).*$", lambda m: f"{m.group(1)}{json.dumps(password)}", client, flags=re.MULTILINE)


def _update_shadowquic_users(cfg: dict[str, Any], runner: RemoteRunner, username: str,
                             password: str, *, remove: bool = False) -> None:
    path = cfg.get("instance_path") or f"/opt/hoplyra/instances/{cfg['id']}"
    container = cfg.get("container_name") or f"cv-sq-{cfg['id'][:8]}"
    meta = {}
    if "meta_json" in cfg and cfg["meta_json"]:
        meta = json.loads(cfg["meta_json"]) if isinstance(cfg["meta_json"], str) else cfg["meta_json"]
    elif "meta" in cfg and isinstance(cfg["meta"], dict):
        meta = cfg["meta"]
    runtime = meta.get("runtime") or cfg.get("runtime")
    port = meta.get("listenPort") or cfg.get("listen_port") or 443
    runtime_bin = "docker" if (runtime and "docker" in runtime) else ("podman" if (runtime and "podman" in runtime) else "")
    script = rf"""
import fcntl, json, os, re, subprocess, time
p = {path + '/config.yaml'!r}
container = {container!r}
port = {port!r}
runtime_bin = {runtime_bin!r}

with open(p + '.lock', 'w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    original = open(p).read()
    match = re.search(r'^(\s*users:\s*)(\[.*?\])\s*$', original, re.M)
    if not match:
        raise RuntimeError('users block missing')
    users = json.loads(match.group(2))
    username, password = {username!r}, {password!r}
    if {remove!r}:
        idx = next((i for i, u in enumerate(users) if u.get('username') == username), None)
        if idx is None or users[idx].get('password') != password:
            raise RuntimeError('peer credentials do not match')
        users.pop(idx)
    else:
        if any(u.get('username') == username for u in users):
            raise RuntimeError('peer already exists')
        users.append({{'username': username, 'password': password}})
    updated = original[:match.start()] + match.group(1) + json.dumps(users) + original[match.end():]
    def write(content):
        with open(p + '.new', 'w') as out:
            os.chmod(p + '.new', 0o600)
            out.write(content)
        os.replace(p + '.new', p)
    write(updated)
    def _get_bin():
        if runtime_bin:
            return runtime_bin
        for b in ('podman', 'docker'):
            try:
                res = subprocess.run([b, 'version'], capture_output=True)
                if res.returncode == 0:
                    return b
            except FileNotFoundError:
                pass
        return 'podman'
    bin_name = _get_bin()
    def _restart():
        try:
            res = subprocess.run([bin_name, 'restart', container], capture_output=True)
            return res.returncode
        except Exception:
            return 1
    def _check_ready(timeout=10.0):
        deadline = time.time() + timeout
        while time.time() <= deadline:
            insp = subprocess.run([bin_name, 'inspect', '-f', '{{{{.State.Status}}}} {{{{.State.Pid}}}}', container], capture_output=True, text=True)
            parts = str(getattr(insp, 'stdout', '') or '').strip().split()
            status = parts[0].lower() if parts else ''
            pid = parts[1] if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) > 0 else None
            if status in ('exited', 'dead'):
                return False
            if status == 'running' and pid:
                ss = subprocess.run(['nsenter', '-t', pid, '-n', 'ss', '-H', '-lun', f'sport = :{{int(port)}}'], capture_output=True, text=True)
                ss_out = str(getattr(ss, 'stdout', '') or '')
                has_port = ss.returncode == 0 and any(
                    len(cols := line.split()) >= 5
                    and (cols[3] if cols[0] in ('UNCONN', 'ESTAB') else (cols[4] if cols[0] == 'udp' else '')).rsplit(':', 1)[-1] == str(port)
                    for line in ss_out.splitlines()
                )
                if has_port:
                    return True
            time.sleep(0.1)
        return False

    restart_code = _restart()
    ready = False
    check_err = None
    if restart_code == 0:
        try:
            ready = _check_ready()
        except Exception as exc:
            check_err = exc
    if not ready:
        write(original)
        rb_restart = _restart()
        if rb_restart != 0:
            raise RuntimeError('container restart failed: rollback restart also failed')
        rb_ready = False
        rb_err = None
        try:
            rb_ready = _check_ready()
        except Exception as exc:
            rb_err = exc
        if not rb_ready:
            detail = rb_err or check_err or 'service did not become ready'
            prefix = 'container restart failed: ' if restart_code != 0 else ''
            raise RuntimeError(
                prefix + 'ShadowQUIC config rolled back and restarted, but previous service readiness could not be verified (' + str(detail) + ')'
            )
        if check_err is not None:
            raise RuntimeError('ShadowQUIC readiness check failed (' + str(check_err) + '); rolled back')
        raise RuntimeError('ShadowQUIC rejected updated config; rolled back')
"""
    code, out, err = runner.run(f"python3 -c {shlex.quote(script)}", timeout=60)
    if code != 0:
        cleaned_err = re.sub(r'password.*', '[REDACTED]', err or out, flags=re.I)
        cleaned_err = cleaned_err.replace(password, '[REDACTED]')
        raise RuntimeError(f"Не удалось обновить ShadowQUIC-клиента: {cleaned_err}")


def _create_shadowquic_peer(cfg: dict[str, Any], runner: RemoteRunner, peer_name: str) -> tuple[str, str]:
    username = f"peer_{uuid.uuid4().hex}"
    password = secrets.token_urlsafe(24)
    client = _shadowquic_client(cfg.get("client_config") or "", username, password, peer_name)
    _update_shadowquic_users(cfg, runner, username, password)
    return username, client


def _remove_shadowquic_peer(cfg: dict[str, Any], runner: RemoteRunner, peer_id: str, password: str) -> None:
    _update_shadowquic_users(cfg, runner, peer_id, password, remove=True)


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
    if protocol not in ("awg", "wg", "xray", "openvpn", "hysteria2", "shadowquic"):
        return {}

    stats: dict[str, dict[str, Any]] = {}
    if protocol in ("awg", "wg"):
        meta = json.loads(cfg.get("meta_json") or "{}")
        is_host_awg = meta.get("hostAwg", True if protocol == "awg" else False)
        is_container = protocol == "wg" or (protocol == "awg" and not is_host_awg)
        tool = "wg" if protocol == "wg" else "awg"
        _, tool_prefix, q_iface = _iface_and_prefix(cfg)
        if is_container:
            container = cfg.get("container_name") or f"cv-{protocol}-{cfg['id'][:8]}"
            cmd = _container_tool_cmd(container, tool, f"show {q_iface} dump")
        else:
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
        if protocol not in ("awg", "wg", "xray", "openvpn", "hysteria2", "shadowquic"):
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
    elif protocol == "hysteria2":
        if not meta.get("username") and not meta.get("password"):
            raise ValueError("Hysteria 2 primary password missing")
        pub_key, client_conf = _create_hysteria2_peer(cfg, runner, name.strip())
        auth_match = re.search(r"^auth: (.+)$", client_conf, re.MULTILINE)
        if not auth_match:
            raise ValueError("Failed to parse auth from Hysteria 2 client config")
        psk = json.loads(auth_match.group(1)).split(":", 1)[1]
        client_ip = pub_key
        if not meta.get("username"):
            primary_password = meta.get("password")
            if not primary_password:
                raise ValueError("Hysteria 2 primary password missing")
            cfg["client_config"] = _hysteria2_client(cfg["client_config"], f"primary:{primary_password}", "Hoplyra-HY2")
            meta["username"] = "primary"
            with connect() as conn:
                conn.execute("UPDATE configs SET client_config = ?, meta_json = ? WHERE id = ?",
                             (cfg["client_config"], json.dumps(meta), config_id))
                conn.commit()
    elif protocol == "shadowquic":
        if not meta.get("password"):
            raise ValueError("ShadowQUIC primary password missing")
        pub_key, client_conf = _create_shadowquic_peer(cfg, runner, name.strip())
        pw_match = re.search(r"^\s*password:\s*(.+)$", client_conf, re.MULTILINE)
        if not pw_match:
            raise ValueError("Failed to parse password from ShadowQUIC client config")
        psk = json.loads(pw_match.group(1))
        client_ip = pub_key
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
    elif protocol == "hysteria2":
        _remove_hysteria2_peer(cfg, runner, peer_row["public_key"], peer_row["preshared_key"])
    elif protocol == "shadowquic":
        _remove_shadowquic_peer(cfg, runner, peer_row["public_key"], peer_row["preshared_key"])
    with connect() as conn:
        conn.execute(
            "UPDATE config_peers SET revoked_at = ? WHERE id = ?",
            (_now_iso(), peer_id),
        )
        conn.commit()
