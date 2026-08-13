from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from hoplyra.remote import RemoteRunner

ProgressCb = Callable[[int, str], None]

SETUP_MARKER = "/opt/hoplyra/.setup-done"

HOST_SETUP_SCRIPT = r"""#!/bin/bash
set -euo pipefail

log() { echo "[hoplyra-setup] $*"; }

has_runtime() {
  command -v podman >/dev/null 2>&1 || command -v docker >/dev/null 2>&1
}

has_compose() {
  podman compose version >/dev/null 2>&1 \
    || command -v podman-compose >/dev/null 2>&1 \
    || docker compose version >/dev/null 2>&1
}

if [ "$(id -u)" -ne 0 ]; then
  log "ERROR: need root (use user root or sudo)"
  exit 1
fi

mkdir -p /opt/hoplyra/instances

sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true
if [ -f /etc/sysctl.conf ] && ! grep -q '^net.ipv4.ip_forward\s*=\s*1' /etc/sysctl.conf; then
  echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf
fi
if [ -d /etc/sysctl.d ] && [ ! -f /etc/sysctl.d/99-hoplyra-forward.conf ]; then
  echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-hoplyra-forward.conf
fi

OS_ID=unknown
OS_LIKE=
if [ -f /etc/os-release ]; then
  . /etc/os-release
  OS_ID="${ID:-unknown}"
  OS_LIKE="${ID_LIKE:-}"
fi

install_base_debian() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq curl ca-certificates iptables iproute2 gnupg lsb-release \
    software-properties-common python3-pip python3 2>/dev/null \
    || apt-get install -y -qq curl ca-certificates iptables iproute2 gnupg python3-pip python3
}

install_podman_debian() {
  install_base_debian
  if ! command -v podman >/dev/null 2>&1; then
    log "installing podman..."
    apt-get install -y -qq podman || apt-get install -y -qq podman podman-docker
  fi
  if ! has_compose; then
    log "installing compose..."
    apt-get install -y -qq podman-compose 2>/dev/null \
      || pip3 install --break-system-packages podman-compose 2>/dev/null \
      || pip3 install podman-compose 2>/dev/null \
      || true
  fi
}

install_docker_debian() {
  install_base_debian
  if ! command -v docker >/dev/null 2>&1; then
    log "installing docker..."
    apt-get install -y -qq docker.io docker-compose-plugin 2>/dev/null \
      || apt-get install -y -qq docker.io
  fi
  systemctl enable docker 2>/dev/null || true
  systemctl start docker 2>/dev/null || true
}

install_rhel_family() {
  if command -v dnf >/dev/null 2>&1; then PM=dnf; else PM=yum; fi
  $PM install -y curl ca-certificates iptables iproute
  if ! command -v podman >/dev/null 2>&1; then
    log "installing podman ($PM)..."
    $PM install -y podman podman-docker 2>/dev/null || $PM install -y podman
  fi
  if ! has_compose; then
    $PM install -y podman-compose 2>/dev/null \
      || pip3 install podman-compose 2>/dev/null || true
  fi
  if ! has_runtime; then
    log "installing docker ($PM)..."
    $PM install -y docker docker-compose-plugin 2>/dev/null || $PM install -y moby-engine docker-compose
    systemctl enable docker 2>/dev/null || true
    systemctl start docker 2>/dev/null || true
  fi
}

install_alt() {
  apt-get update -qq
  apt-get install -y podman podman-compose curl ca-certificates iptables iproute2 \
    python3 python3-pip 2>/dev/null \
    || apt-get install -y podman curl ca-certificates iptables iproute2 python3
  if ! has_compose; then
    pip3 install podman-compose 2>/dev/null || true
  fi
}

log "detected OS: ${OS_ID} (${OS_LIKE})"

case "$OS_ID" in
  debian|ubuntu|linuxmint|pop)
    install_podman_debian
    ;;
  alt|altlinux|basealt)
    install_alt
    ;;
  rhel|centos|rocky|almalinux|fedora|ol|amzn)
    install_rhel_family
    ;;
  *)
    if echo "$OS_LIKE" | grep -qiE 'debian|ubuntu'; then
      install_podman_debian
    elif echo "$OS_LIKE" | grep -qiE 'rhel|fedora|centos'; then
      install_rhel_family
    else
      log "unknown OS, trying debian-style install..."
      install_podman_debian || install_docker_debian || true
    fi
    ;;
esac

if ! has_runtime; then
  log "podman missing, fallback to docker (debian)..."
  install_docker_debian || true
fi

if ! has_runtime; then
  log "ERROR: could not install podman or docker"
  exit 2
fi

if ! has_compose; then
  log "ERROR: container runtime found but compose missing"
  podman --version 2>/dev/null || docker --version 2>/dev/null || true
  exit 3
fi

chmod 755 /opt/hoplyra /opt/hoplyra/instances
date -Iseconds > /opt/hoplyra/.setup-done

log "ready:"
podman --version 2>/dev/null || true
docker --version 2>/dev/null || true
podman compose version 2>/dev/null | head -1 || podman-compose --version 2>/dev/null || docker compose version 2>/dev/null | head -1 || true
"""


def container_runtime_ready(runner: RemoteRunner) -> bool:
    code, _, _ = runner.run(
        "(command -v podman >/dev/null || command -v docker >/dev/null) && "
        "(podman compose version >/dev/null 2>&1 || "
        "command -v podman-compose >/dev/null || "
        "docker compose version >/dev/null 2>&1)"
    )
    return code == 0


def get_runtime_info(runner: RemoteRunner) -> str | None:
    code, out, _ = runner.run(
        "podman --version 2>/dev/null; "
        "podman compose version 2>/dev/null | head -1; "
        "command -v podman-compose >/dev/null && podman-compose --version 2>/dev/null; "
        "docker compose version 2>/dev/null | head -1"
    )
    if code != 0 or not out.strip():
        return None
    return out.strip().split("\n")[0][:120]


def ensure_host_ready(
    runner: RemoteRunner,
    force: bool = False,
    on_progress: ProgressCb | None = None,
) -> dict:

    def prog(percent: int, message: str) -> None:
        if on_progress:
            on_progress(percent, message)

    if runner.target.is_local:
        return {"prepared": True, "message": "local skip", "runtime": get_runtime_info(runner)}

    prog(15, "Проверка Podman/Docker на VPS…")

    if not force:
        code, out, _ = runner.run(f"test -f {SETUP_MARKER} && echo ok", timeout=10)
        if code == 0 and "ok" in out:
            prog(100, "Сервер готов")
            return {"prepared": True, "message": "already ready", "runtime": "podman/docker"}

    if not force and container_runtime_ready(runner):

        prog(55, "Контейнерный runtime найден, настройка каталогов…")
        runner.run(
            "mkdir -p /opt/hoplyra/instances && "
            "sysctl -w net.ipv4.ip_forward=1 2>/dev/null || true && "
            f"date -Iseconds > {SETUP_MARKER}"
        )
        prog(100, "Сервер уже подготовлен")
        return {
            "prepared": True,
            "message": "already ready",
            "runtime": get_runtime_info(runner),
        }

    prog(28, "Загрузка скрипта установки на VPS…")
    runner.upload_text("/tmp/hoplyra-setup.sh", HOST_SETUP_SCRIPT, 0o755)
    prog(38, "Установка Podman и Docker (обычно 5–15 минут)…")

    stop_ticker = threading.Event()

    def ticker() -> None:
        p = 38
        while not stop_ticker.wait(10):
            p = min(p + 4, 88)
            prog(p, "Установка пакетов на VPS…")

    tick = threading.Thread(target=ticker, daemon=True)
    tick.start()
    try:
        code, out, err = runner.run("bash /tmp/hoplyra-setup.sh 2>&1", timeout=1200)
    finally:
        stop_ticker.set()

    combined = (out + "\n" + err).strip()

    if code != 0:
        tail = combined[-1000:] if combined else f"exit code {code}"
        raise RuntimeError(f"Подготовка VPS не удалась: {tail}")

    prog(92, "Проверка установки…")

    if not container_runtime_ready(runner):
        raise RuntimeError(
            "Скрипт завершился, но Podman/Docker Compose всё ещё недоступен. "
            + (combined[-400:] if combined else "")
        )

    prog(100, "Подготовка завершена")
    return {
        "prepared": True,
        "message": "installed",
        "runtime": get_runtime_info(runner),
        "log": combined[-600:] if combined else None,
    }
