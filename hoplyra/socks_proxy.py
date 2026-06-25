from __future__ import annotations

import json
import logging
import secrets
import shlex
from typing import Any, Literal

log = logging.getLogger(__name__)

from hoplyra.chains.deployer import _chain_tag
from hoplyra.remote import RemoteRunner, mkdir_remote, podman_compose_down, podman_compose_up
from hoplyra.secrets import decrypt_auth_secret, encrypt_auth_secret
from hoplyra.socks_runtime import (
    SOCKS_MOUNT,
    build_3proxy_config,
    compose_socks_service,
    ensure_socks_image,
)

CLIENT_SOCKS_TAG = "client-socks"
SOCKS_PROXY_SUBDIR = "socks-proxy"
DEFAULT_SOCKS_PORT_BASE = 41080
DEFAULT_SOCKS_PORT_SPAN = 920

SocksMode = Literal["3proxy", "gateway"]


class SocksProxyError(Exception):
    pass


def socks_port_for_config(config_id: str) -> int:
    tail = config_id.replace("-", "")[:8]
    try:
        n = int(tail, 16) if tail else 0
    except ValueError:
        n = sum(ord(c) for c in config_id)
    return DEFAULT_SOCKS_PORT_BASE + (n % DEFAULT_SOCKS_PORT_SPAN)


def _gen_credentials() -> tuple[str, str]:
    user = f"hop{secrets.token_hex(3)}"
    password = secrets.token_hex(10)
    return user, password


def _socks_container_name(config_id: str) -> str:
    return f"cv-socks-{_chain_tag(config_id)}"


def _socks_compose_project(config_id: str) -> str:
    return f"cv-socks-{_chain_tag(config_id)}"


def _socks_proxy_dir(instance_path: str) -> str:
    return f"{instance_path}/{SOCKS_PROXY_SUBDIR}"


def _has_exit_outbound(xray_cfg: dict[str, Any]) -> bool:
    return any(o.get("tag") == "exit" for o in xray_cfg.get("outbounds", []))


def _socks_outbound_tag(xray_cfg: dict[str, Any]) -> str:
    outbounds = xray_cfg.get("outbounds", [])
    tags = {o.get("tag") for o in outbounds if o.get("tag")}
    if "exit" in tags:
        return "exit"
    if "direct" in tags:
        return "direct"
    for outbound in outbounds:
        if outbound.get("protocol") in ("freedom", "direct"):
            tag = outbound.get("tag")
            if tag:
                return tag
    for outbound in outbounds:
        tag = outbound.get("tag")
        if tag and outbound.get("protocol") != "blackhole":
            return tag
    raise SocksProxyError(
        "В конфигурации Xray нет исходящего outbound для SOCKS (нужен exit или direct)"
    )


def _can_use_socks_gateway(xray_cfg: dict[str, Any]) -> bool:
    try:
        _socks_outbound_tag(xray_cfg)
        return True
    except SocksProxyError:
        return False


def _remote_file_exists(runner: RemoteRunner, path: str) -> bool:
    code, _, _ = runner.run(f"test -f {shlex.quote(path)}")
    return code == 0


def _container_exists(runner: RemoteRunner, name: str) -> bool:
    q = shlex.quote(name)
    code, out, _ = runner.run(
        f"(podman ps -a --format '{{{{.Names}}}}' 2>/dev/null || "
        f"docker ps -a --format '{{{{.Names}}}}' 2>/dev/null) | grep -Fx {q}",
        timeout=30,
    )
    return code == 0 and bool(out.strip())


def _pick_running_container(runner: RemoteRunner, candidates: list[str | None]) -> str:
    for name in candidates:
        if name and _container_exists(runner, name):
            return name
    for name in candidates:
        if name:
            return name
    raise SocksProxyError("Не найден контейнер для перезапуска")


def _resolve_socks_mode(
    *,
    instance_path: str,
    runner: RemoteRunner,
    protocol: str | None,
    meta: dict[str, Any] | None,
) -> SocksMode:
    if (meta or {}).get("socksProxy", {}).get("mode") in ("3proxy", "gateway"):
        return meta["socksProxy"]["mode"]  # type: ignore[return-value]

    for cfg_path in (
        f"{instance_path}/config.json",
        f"{instance_path}/entry-config.json",
    ):
        if not _remote_file_exists(runner, cfg_path):
            continue
        try:
            xray_cfg = _read_xray_config(runner, cfg_path)
        except SocksProxyError:
            continue
        if _can_use_socks_gateway(xray_cfg):
            return "gateway"

    if protocol in ("awg", "wg", "openvpn") and not (meta or {}).get("chain"):
        return "3proxy"

    if _remote_file_exists(runner, f"{instance_path}/config.json"):
        return "gateway"

    raise SocksProxyError(
        "SOCKS5 недоступен: нет Xray gateway и не поддерживается для этого VPN"
    )


def _remove_client_socks(xray_cfg: dict[str, Any]) -> dict[str, Any]:
    xray_cfg["inbounds"] = [
        ib for ib in xray_cfg.get("inbounds", []) if ib.get("tag") != CLIENT_SOCKS_TAG
    ]
    routing = xray_cfg.setdefault("routing", {"domainStrategy": "AsIs", "rules": []})
    routing["rules"] = [
        rule
        for rule in routing.get("rules", [])
        if CLIENT_SOCKS_TAG not in rule.get("inboundTag", [])
    ]
    return xray_cfg


def _add_client_socks(xray_cfg: dict[str, Any], *, port: int, user: str, password: str) -> dict[str, Any]:
    outbound = _socks_outbound_tag(xray_cfg)

    xray_cfg = _remove_client_socks(xray_cfg)
    xray_cfg.setdefault("inbounds", []).append(
        {
            "tag": CLIENT_SOCKS_TAG,
            "port": port,
            "listen": "0.0.0.0",
            "protocol": "socks",
            "settings": {
                "auth": "password",
                "accounts": [{"user": user, "pass": password}],
                "udp": True,
            },
            "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
        }
    )

    routing = xray_cfg.setdefault("routing", {"domainStrategy": "UseIPv4", "rules": []})
    rules: list[dict[str, Any]] = [
        rule
        for rule in routing.get("rules", [])
        if CLIENT_SOCKS_TAG not in rule.get("inboundTag", [])
    ]
    has_exit_direct = any(o.get("tag") == "exit-direct" for o in xray_cfg.get("outbounds", []))
    if has_exit_direct and outbound == "exit":
        rules.insert(
            0,
            {
                "type": "field",
                "network": "udp",
                "inboundTag": [CLIENT_SOCKS_TAG],
                "outboundTag": "exit-direct",
            },
        )
        rules.insert(
            1,
            {
                "type": "field",
                "network": "tcp",
                "inboundTag": [CLIENT_SOCKS_TAG],
                "outboundTag": "exit",
            },
        )
    else:
        rules.insert(
            0,
            {
                "type": "field",
                "inboundTag": [CLIENT_SOCKS_TAG],
                "outboundTag": outbound,
            },
        )
    routing["rules"] = rules
    return xray_cfg


def resolve_gateway_target(
    *,
    config_id: str,
    instance_path: str,
    container_name: str | None,
    runner: RemoteRunner,
    meta: dict[str, Any] | None,
) -> tuple[str, str]:
    tag = _chain_tag(config_id)
    socks_meta = (meta or {}).get("socksProxy") or {}
    stored_container = socks_meta.get("container")
    if stored_container and stored_container.startswith("cv-chain-"):
        stored_container = None
    stored_cfg = socks_meta.get("configPath")
    if stored_cfg and _remote_file_exists(runner, stored_cfg):
        cfg_path = stored_cfg
    else:
        cfg_path = None
        for candidate in (f"{instance_path}/config.json", f"{instance_path}/entry-config.json"):
            if _remote_file_exists(runner, candidate):
                cfg_path = candidate
                break
        if not cfg_path:
            raise SocksProxyError("Не найден config.json Xray gateway")

    xray_cfg = _read_xray_config(runner, cfg_path)
    _socks_outbound_tag(xray_cfg)

    container = _pick_running_container(
        runner,
        [
            stored_container,
            f"cv-xentry-{tag}",
            f"cv-gateway-{tag}",
            f"cv-xray-{config_id[:8]}",
            container_name if container_name and not container_name.startswith("cv-chain-") else None,
            container_name,
        ],
    )
    return cfg_path, container


def _deploy_3proxy_container(
    runner: RemoteRunner,
    *,
    config_id: str,
    instance_path: str,
    port: int,
    username: str,
    password: str,
) -> str:
    proxy_dir = _socks_proxy_dir(instance_path)
    mkdir_remote(runner, proxy_dir)
    container = _socks_container_name(config_id)

    runner.upload_text(
        f"{proxy_dir}/3proxy.cfg",
        build_3proxy_config(port=port, username=username, password=password),
    )
    ensure_socks_image(runner)
    compose = f"services:\n{compose_socks_service(container, proxy_dir)}\n"
    runner.upload_text(f"{proxy_dir}/docker-compose.yml", compose)
    podman_compose_up(runner, proxy_dir, _socks_compose_project(config_id))
    return container


def _teardown_3proxy_container(
    runner: RemoteRunner,
    *,
    config_id: str,
    instance_path: str,
    fast: bool = False,
) -> None:
    container = _socks_container_name(config_id)
    q = shlex.quote(container)
    runner.run(
        f"podman rm -f {q} 2>/dev/null || docker rm -f {q} 2>/dev/null || true",
        timeout=30,
    )
    if not fast:
        proxy_dir = _socks_proxy_dir(instance_path)
        if _remote_file_exists(runner, f"{proxy_dir}/docker-compose.yml"):
            podman_compose_down(runner, proxy_dir, _socks_compose_project(config_id), timeout=15)


def _cleanup_stale_socks_containers(runner: RemoteRunner, *, keep: str | None = None) -> None:
    code, out, _ = runner.run(
        "podman ps -a --format '{{.Names}}' 2>/dev/null | grep '^cv-socks-' || true"
    )
    stale = [name.strip() for name in out.splitlines() if name.strip() and name.strip() != keep]
    if stale:
        quoted = " ".join(shlex.quote(name) for name in stale)
        runner.run(
            f"podman rm -f {quoted} 2>/dev/null || docker rm -f {quoted} 2>/dev/null || true",
            timeout=60,
        )
    runner.run(
        "iptables-save 2>/dev/null | awk '/hoplyra-socks/ && /^-A/ "
        "{sub(/^-A/,\"-D\"); print}' | while read -r rule; do "
        "iptables $rule 2>/dev/null || true; done; true",
        timeout=60,
    )


def _teardown_legacy_xray_sidecar(runner: RemoteRunner, config_id: str, instance_path: str) -> None:
    legacy_dir = f"{instance_path}/socks-gateway"
    if _remote_file_exists(runner, f"{legacy_dir}/docker-compose.yml"):
        podman_compose_down(runner, legacy_dir, f"cv-socks-{_chain_tag(config_id)}")
    _cleanup_stale_socks_containers(runner, keep=_socks_container_name(config_id))


def _read_xray_config(runner: RemoteRunner, path: str) -> dict[str, Any]:
    code, out, err = runner.run(f"cat {shlex.quote(path)}")
    if code != 0 or not out.strip():
        raise SocksProxyError(err or f"Не удалось прочитать {path}")
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise SocksProxyError(f"Некорректный JSON в {path}") from exc


def _write_xray_config(runner: RemoteRunner, path: str, cfg: dict[str, Any]) -> None:
    runner.upload_text(path, json.dumps(cfg, indent=2))


def _restart_container(runner: RemoteRunner, container: str) -> None:
    q = shlex.quote(container)
    code, _, err = runner.run(
        f"podman restart {q} 2>/dev/null || docker restart {q} 2>/dev/null || true"
    )
    if code != 0:
        raise SocksProxyError(err or f"Не удалось перезапустить контейнер {container}")


def enable_socks_proxy(
    *,
    config_id: str,
    instance_path: str,
    container_name: str | None,
    entry_host: str,
    runner: RemoteRunner,
    meta: dict[str, Any],
    protocol: str | None = None,
    client_ip: str | None = None,
) -> dict[str, Any]:
    del client_ip
    _teardown_legacy_xray_sidecar(runner, config_id, instance_path)

    existing = meta.get("socksProxy") or {}
    port = int(existing.get("port") or socks_port_for_config(config_id))
    user, password = _gen_credentials()
    mode = _resolve_socks_mode(instance_path=instance_path, runner=runner, protocol=protocol, meta=meta)

    if mode == "3proxy":
        _teardown_3proxy_container(runner, config_id=config_id, instance_path=instance_path)
        container = _deploy_3proxy_container(
            runner,
            config_id=config_id,
            instance_path=instance_path,
            port=port,
            username=user,
            password=password,
        )
        config_path = f"{_socks_proxy_dir(instance_path)}/3proxy.cfg"
    else:
        cfg_path, container = resolve_gateway_target(
            config_id=config_id,
            instance_path=instance_path,
            container_name=container_name,
            runner=runner,
            meta=meta,
        )
        xray_cfg = _read_xray_config(runner, cfg_path)
        patched = _add_client_socks(xray_cfg, port=port, user=user, password=password)
        _write_xray_config(runner, cfg_path, patched)
        _restart_container(runner, container)
        config_path = cfg_path

    return {
        "enabled": True,
        "mode": mode,
        "host": entry_host,
        "port": port,
        "username": user,
        "passwordEnc": encrypt_auth_secret(password),
        "container": container,
        "configPath": config_path,
        "_password_once": password,
    }


def disable_socks_proxy(
    *,
    config_id: str,
    instance_path: str,
    container_name: str | None,
    runner: RemoteRunner,
    meta: dict[str, Any],
    protocol: str | None = None,
    client_ip: str | None = None,
) -> None:
    del client_ip
    socks = meta.get("socksProxy") or {}
    if not socks.get("enabled"):
        return

    mode = socks.get("mode") or _resolve_socks_mode(
        instance_path=instance_path,
        runner=runner,
        protocol=protocol,
        meta=meta,
    )

    if mode == "3proxy":
        _teardown_3proxy_container(runner, config_id=config_id, instance_path=instance_path, fast=True)
    else:
        cfg_path, container = resolve_gateway_target(
            config_id=config_id,
            instance_path=instance_path,
            container_name=container_name or socks.get("container"),
            runner=runner,
            meta=meta,
        )
        xray_cfg = _read_xray_config(runner, cfg_path)
        patched = _remove_client_socks(xray_cfg)
        _write_xray_config(runner, cfg_path, patched)
        _restart_container(runner, container)

    legacy_dir = f"{instance_path}/socks-gateway"
    if _remote_file_exists(runner, f"{legacy_dir}/docker-compose.yml"):
        podman_compose_down(runner, legacy_dir, f"cv-socks-{_chain_tag(config_id)}", timeout=15)


def _resolve_socks_password(socks: dict[str, Any]) -> str | None:
    password = socks.get("password")
    if password:
        return str(password)
    enc = socks.get("passwordEnc")
    if not enc:
        return None
    try:
        return decrypt_auth_secret(str(enc))
    except RuntimeError as exc:
        log.warning("SOCKS password decrypt failed: %s", exc)
        return None


def socks_proxy_for_response(meta: dict[str, Any]) -> dict[str, Any] | None:
    socks = meta.get("socksProxy")
    if not socks or not socks.get("enabled"):
        return None
    password = _resolve_socks_password(socks)
    return {
        "enabled": True,
        "mode": socks.get("mode"),
        "host": socks.get("host"),
        "port": socks.get("port"),
        "username": socks.get("username"),
        "password": password,
        "uri": _socks_uri(socks.get("username"), password, socks.get("host"), socks.get("port")),
    }


def _socks_uri(user: str | None, password: str | None, host: str | None, port: int | None) -> str | None:
    if not user or not password or not host or not port:
        return None
    from urllib.parse import quote

    return f"socks5://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
