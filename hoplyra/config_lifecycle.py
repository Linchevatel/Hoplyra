from __future__ import annotations

import json
import logging
import shlex
from typing import Any

from hoplyra.awg_runtime import awg_quick_down, chain_tag_from_config_id, discover_awg_confs
from hoplyra.host_cleanup import purge_host_artifacts, verify_config_purged
from hoplyra.protocols import default_instance_path, get_deployer_for_config
from hoplyra.remote import RemoteRunner, is_local_host, podman_compose_down
from hoplyra.socks_proxy import _socks_container_name, _socks_compose_project, _socks_proxy_dir

log = logging.getLogger("hoplyra")


def chain_server_ids(cfg: dict[str, Any]) -> list[str]:
    meta = json.loads(cfg.get("meta_json") or "{}")
    if not meta.get("chain"):
        sid = cfg.get("server_id")
        return [sid] if sid else []
    hops = meta.get("hops") or []
    return list(dict.fromkeys(h.get("serverId") for h in hops if h.get("serverId")))


def _chain_root(config_id: str) -> str:
    return f"/opt/hoplyra/chains/{config_id}"


def _run_chain_compose(runner: RemoteRunner, root: str, *, action: str) -> None:
    root_q = shlex.quote(root)
    if action == "down":
        cmd = (
            f"if [ -d {root_q} ]; then "
            f"find {root_q} -name docker-compose.yml 2>/dev/null | while read -r f; do "
            f'  d=$(dirname "$f"); '
            f'  (cd "$d" && (podman compose down 2>/dev/null || podman-compose down 2>/dev/null || true)); '
            f"done; fi"
        )
    else:
        cmd = (
            f"if [ -d {root_q} ]; then "
            f"find {root_q} -name docker-compose.yml 2>/dev/null | while read -r f; do "
            f'  d=$(dirname "$f"); '
            f'  (cd "$d" && (podman compose up -d 2>/dev/null || podman-compose up -d 2>/dev/null || true)); '
            f"done; fi"
        )
    runner.run(cmd, timeout=300)


def stop_remote_config(
    cfg: dict[str, Any],
    *,
    get_server_row,
    server_target,
    purge: bool = False,
    server_ids: list[str] | None = None,
    strict: bool = False,
) -> None:
    meta = json.loads(cfg.get("meta_json") or "{}")
    config_id = cfg["id"]

    if meta.get("chain"):
        tag = chain_tag_from_config_id(config_id)
        root = _chain_root(config_id)
        chain_servers = server_ids or chain_server_ids(cfg)
        cleanup_errors: list[str] = []
        for sid in chain_servers:
            row = get_server_row(sid)
            runner = RemoteRunner(server_target(row))
            try:
                for conf in discover_awg_confs(runner, config_id):
                    awg_quick_down(runner, conf)
                _run_chain_compose(runner, root, action="down")
                if purge:
                    purge_host_artifacts(runner, config_id, chain=True)
                    root_q = shlex.quote(root)
                    runner.run(
                        f"podman ps -a --format '{{{{.Names}}}}' 2>/dev/null | grep -E 'cv-.+-{tag}' | "
                        f"xargs -r podman rm -f 2>/dev/null || true; "
                        f"rm -rf {root_q}",
                        timeout=300,
                    )
                    if strict:
                        issues = verify_config_purged(runner, config_id, chain=True)
                        if issues:
                            cleanup_errors.extend(f"{sid}: {issue}" for issue in issues)
            except Exception as exc:
                if strict:
                    raise
                log.warning("Chain stop %s on %s failed: %s", config_id, sid, exc)
        if strict and cleanup_errors:
            raise RuntimeError("; ".join(cleanup_errors))
        return

    if not cfg.get("instance_path"):
        if not cfg.get("server_id"):
            return
        row = get_server_row(cfg["server_id"])
        instance_path = default_instance_path(config_id, is_local=is_local_host(row["host"]))
    else:
        instance_path = cfg["instance_path"]
        row = get_server_row(cfg["server_id"])

    runner = RemoteRunner(server_target(row))
    socks_container = _socks_container_name(config_id)
    runner.run(
        f"podman rm -f {shlex.quote(socks_container)} 2>/dev/null || "
        f"docker rm -f {shlex.quote(socks_container)} 2>/dev/null || true",
        timeout=30,
    )
    socks_dir = _socks_proxy_dir(instance_path)
    socks_compose = f"{socks_dir}/docker-compose.yml"
    code, _, _ = runner.run(f"test -f {shlex.quote(socks_compose)}", timeout=15)
    if code == 0:
        podman_compose_down(runner, socks_dir, _socks_compose_project(config_id), timeout=15)

    deployer = get_deployer_for_config(cfg["protocol"], meta)
    deployer.stop(runner, config_id, instance_path)
    if purge:
        purge_host_artifacts(runner, config_id, chain=False)
        container = cfg.get("container_name") or f"cv-{cfg['protocol']}-{config_id[:8]}"
        runner.run(
            f"podman rm -f {shlex.quote(container)} 2>/dev/null || true",
            timeout=120,
        )
        runner.run(f"rm -rf {shlex.quote(instance_path)}", timeout=120)
        if strict:
            issues = verify_config_purged(
                runner,
                config_id,
                chain=False,
                instance_path=instance_path,
            )
            if issues:
                raise RuntimeError("; ".join(issues))


def restart_remote_config(cfg: dict[str, Any], *, get_server_row, server_target) -> None:
    meta = json.loads(cfg.get("meta_json") or "{}")
    config_id = cfg["id"]

    if meta.get("chain"):
        root = _chain_root(config_id)
        for sid in chain_server_ids(cfg):
            row = get_server_row(sid)
            runner = RemoteRunner(server_target(row))
            _run_chain_compose(runner, root, action="up")
        return

    if not cfg.get("instance_path"):
        raise ValueError("No instance path")

    row = get_server_row(cfg["server_id"])
    runner = RemoteRunner(server_target(row))
    project = f"cv-{cfg['protocol']}-{config_id[:8]}"
    path_q = shlex.quote(cfg["instance_path"])
    code, out, err = runner.run(
        f"cd {path_q} && "
        f"(podman compose -p {project} up -d --force-recreate || "
        f"podman-compose -p {project} up -d || "
        f"docker compose -p {project} up -d --force-recreate)",
        timeout=300,
    )
    if code != 0:
        raise RuntimeError((err or out or "restart failed").strip())
