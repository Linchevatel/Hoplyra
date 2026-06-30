from __future__ import annotations

import ipaddress
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import paramiko

from hoplyra.host_setup import ensure_host_ready, get_runtime_info, container_runtime_ready

_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain"})
_SSH_CONNECT_ATTEMPTS = 5
_SSH_MAX_INFLIGHT = 4
_SSH_GATE = threading.BoundedSemaphore(_SSH_MAX_INFLIGHT)


def is_local_host(host: str) -> bool:
    h = host.strip().lower()
    if h in _LOCAL_NAMES or h == "::1":
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_loopback or ip.is_link_local or ip.is_unspecified
    except ValueError:
        return False


@dataclass
class ServerTarget:
    host: str
    port: int
    username: str
    auth_type: str = "password"
    auth_secret: str | None = None

    @property
    def is_local(self) -> bool:
        return is_local_host(self.host)


class _PasswordOnlyAuth:

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password

    def authenticate(self, transport: paramiko.Transport) -> None:
        transport.auth_password(self.username, self.password)


def _ssh_timeouts() -> tuple[float, float, float]:
    if sys.platform == "win32":
        return 30.0, 60.0, 30.0
    return 25.0, 60.0, 25.0


def _open_tcp_socket(host: str, port: int, *, timeout: float) -> socket.socket:
    errors: list[OSError] = []
    families = (socket.AF_INET, socket.AF_UNSPEC)

    for family in families:
        try:
            infos = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
        except OSError as exc:
            errors.append(exc)
            continue
        for info in infos:
            fam, socktype, proto, _, addr = info
            sock = socket.socket(fam, socktype, proto)
            sock.settimeout(timeout)
            try:
                sock.connect(addr)
                return sock
            except OSError as exc:
                errors.append(exc)
                sock.close()

    if errors:
        raise errors[-1]
    raise OSError(f"Cannot connect to {host}:{port}")


def _connect_kwargs(target: ServerTarget) -> dict:
    if not target.auth_secret:
        raise ValueError("SSH password is required")

    connect_timeout, banner_timeout, auth_timeout = _ssh_timeouts()
    return {
        "hostname": target.host,
        "port": target.port,
        "username": target.username,
        "timeout": connect_timeout,
        "banner_timeout": banner_timeout,
        "auth_timeout": auth_timeout,
        "auth_strategy": _PasswordOnlyAuth(target.username, target.auth_secret),
        "allow_agent": False,
        "look_for_keys": False,
    }


def _friendly_ssh_error(exc: Exception, *, username: str = "root", host: str = "") -> str:
    msg = str(exc).strip()[:500]
    lower = msg.lower()
    if "banner" in lower or "eof" in lower:
        hint_host = host or "<IP>"
        platform_hint = (
            "разрешите hoplyra-backend.exe в брандмауэре Windows"
            if sys.platform == "win32"
            else "проверьте доступность SSH с этой машины"
        )
        return (
            f"{msg}. Проверьте IP и SSH-порт, {platform_hint} "
            f"и проверьте доступ: ssh {username}@{hint_host}"
        )
    return msg


def _is_transient_ssh_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(token in msg for token in ("banner", "eof", "timed out", "timeout", "connection reset"))


def _connect_ssh_once(client: paramiko.SSHClient, target: ServerTarget) -> None:
    kwargs = _connect_kwargs(target)
    connect_timeout = float(kwargs.pop("timeout"))
    banner_timeout = float(kwargs.pop("banner_timeout"))
    auth_timeout = float(kwargs.pop("auth_timeout"))
    sock = _open_tcp_socket(target.host, target.port, timeout=connect_timeout)
    client.connect(
        timeout=connect_timeout,
        banner_timeout=banner_timeout,
        auth_timeout=auth_timeout,
        sock=sock,
        **kwargs,
    )


def _sshpass_available() -> bool:
    if sys.platform == "win32":
        return False
    return shutil.which("sshpass") is not None and shutil.which("ssh") is not None


def _run_ssh_subprocess(target: ServerTarget, command: str, timeout: int) -> tuple[int, str, str]:
    if not target.auth_secret:
        raise ValueError("SSH password is required")
    proc = subprocess.run(
        [
            "sshpass",
            "-p",
            target.auth_secret,
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            f"ConnectTimeout={min(max(timeout, 5), 30)}",
            "-p",
            str(target.port),
            f"{target.username}@{target.host}",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


@contextmanager
def ssh_client(target: ServerTarget) -> Iterator[paramiko.SSHClient]:
    if target.is_local:
        yield None                      
        return

    last_exc: Exception | None = None
    with _SSH_GATE:
        for attempt in range(_SSH_CONNECT_ATTEMPTS):
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                _connect_ssh_once(client, target)
            except (TimeoutError, paramiko.SSHException, OSError) as exc:
                last_exc = exc
                try:
                    client.close()
                except Exception:
                    pass
                if attempt + 1 < _SSH_CONNECT_ATTEMPTS and _is_transient_ssh_error(exc):
                    time.sleep(min(3.0 * (attempt + 1), 12.0))
                    continue
                raise
            try:
                yield client
            finally:
                client.close()
            return
    if last_exc is not None:
        raise last_exc


class RemoteRunner:
    def __init__(self, target: ServerTarget) -> None:
        self.target = target

    def run(self, command: str, timeout: int = 300, retries: int = 3) -> tuple[int, str, str]:
        if self.target.is_local:
            proc = subprocess.run(
                ["bash", "-lc", command],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return proc.returncode, proc.stdout, proc.stderr

        last_exc: Exception | None = None
        for attempt in range(max(1, retries)):
            try:
                with ssh_client(self.target) as client:
                    _, stdout, stderr = client.exec_command(command, timeout=timeout)
                    out = stdout.read().decode(errors="replace")
                    err = stderr.read().decode(errors="replace")
                    code = stdout.channel.recv_exit_status()
                    return code, out, err
            except (TimeoutError, paramiko.SSHException, OSError) as exc:
                last_exc = exc
                if attempt + 1 < retries:
                    time.sleep(min(5 * (attempt + 1), 15))
                    continue

        if _sshpass_available():
            try:
                return _run_ssh_subprocess(self.target, command, timeout=timeout)
            except (TimeoutError, OSError, subprocess.SubprocessError) as exc:
                last_exc = exc

        assert last_exc is not None
        raise last_exc

    def upload_text(self, remote_path: str, content: str, mode: int = 0o644) -> None:
        if self.target.is_local:
            from pathlib import Path

            p = Path(remote_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            p.chmod(mode)
            return

        parent = remote_path.rsplit("/", 1)[0]
        with ssh_client(self.target) as client:
            sftp = client.open_sftp()
            try:
                sftp.stat(parent)
            except FileNotFoundError:
                mkdir_remote(self, parent)
            with sftp.file(remote_path, "w") as f:
                f.write(content)
            sftp.chmod(remote_path, mode)
            sftp.close()

    def upload_file(self, remote_path: str, local_path: Path | str, *, mode: int = 0o644) -> None:
        src = Path(local_path)
        if not src.is_file():
            raise FileNotFoundError(str(src))

        if self.target.is_local:
            dst = Path(remote_path)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            dst.chmod(mode)
            return

        parent = remote_path.rsplit("/", 1)[0]
        with ssh_client(self.target) as client:
            sftp = client.open_sftp()
            try:
                sftp.stat(parent)
            except FileNotFoundError:
                mkdir_remote(self, parent)
            sftp.put(str(src), remote_path)
            sftp.chmod(remote_path, mode)
            sftp.close()


def test_ssh_auth(target: ServerTarget) -> tuple[bool, str | None]:
    if target.is_local:
        return True, None
    try:
        runner = RemoteRunner(target)
        code, out, err = runner.run("echo HOPLYRA_OK", timeout=30)
        if code == 0 and "HOPLYRA_OK" in out:
            return True, None
        detail = (err or out or "SSH authentication failed").strip()
        return False, detail[:500]
    except Exception as exc:
        if _sshpass_available() and _is_transient_ssh_error(exc):
            try:
                code, out, err = _run_ssh_subprocess(target, "echo HOPLYRA_OK", timeout=30)
                if code == 0 and "HOPLYRA_OK" in out:
                    return True, None
                detail = (err or out or "SSH authentication failed").strip()
                return False, detail[:500]
            except Exception as fallback_exc:
                exc = fallback_exc
        msg = _friendly_ssh_error(exc, username=target.username, host=target.host)
        if "Private key" in msg or "encrypted" in msg.lower():
            return False, "Укажите пароль SSH пользователя (root), не ключ."
        return False, msg


def measure_tcp_latency(host: str, port: int = 22, timeout: float = 10.0) -> int | None:
    try:
        start = time.perf_counter()
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return max(1, round((time.perf_counter() - start) * 1000))
    except OSError:
        return None


def probe_server(target: ServerTarget) -> dict:
    latency_ms: int | None = None if target.is_local else measure_tcp_latency(target.host, target.port)
    os_info: str | None = None
    podman_version: str | None = None
    message: str | None = None
    status = "online"

    try:
        if latency_ms is None and not target.is_local:
            raise OSError(f"Порт {target.port} недоступен")

        ssh_ok, ssh_err = test_ssh_auth(target)
        if not ssh_ok:
            return {
                "status": "offline",
                "latencyMs": latency_ms,
                "os": None,
                "podmanVersion": None,
                "message": ssh_err or "SSH недоступен",
                "lastSeen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

        runner = RemoteRunner(target)
        code, out, _ = runner.run("uname -sr 2>/dev/null || head -1 /etc/os-release")
        if code == 0:
            os_info = out.strip().split("\n")[0][:120]

        if container_runtime_ready(runner):
            podman_version = get_runtime_info(runner)
        else:
            message = "Контейнерный runtime не установлен — будет установлен при деплое или ping"
            status = "connecting"

    except OSError as exc:
        status = "offline"
        latency_ms = None
        message = f"Порт {target.port} недоступен: {exc}"
    except Exception as exc:
        status = "offline"
        message = str(exc)[:500]

    return {
        "status": status,
        "latencyMs": latency_ms,
        "os": os_info,
        "podmanVersion": podman_version,
        "message": message,
        "lastSeen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def prepare_host(runner: RemoteRunner) -> None:
    ensure_host_ready(runner)


def ensure_podman(runner: RemoteRunner) -> None:
    ensure_host_ready(runner)
    if not container_runtime_ready(runner):
        raise RuntimeError("Podman/Docker Compose недоступен после подготовки VPS")


def detect_wan_iface_script() -> str:
    return "$(ip -4 route show default 2>/dev/null | awk '/default/ {print $5; exit}')"


def shell_path(path: str) -> str:
    return shlex.quote(path)


def mkdir_remote(runner: RemoteRunner, path: str) -> None:
    runner.run(f"mkdir -p {shell_path(path)}")


def wan_wg_forward_up_cmd(wg_iface: str = "wg0") -> str:
    wan = detect_wan_iface_script()
    return (
        f"WAN={wan}; "
        f'[ -n "$WAN" ] && (iptables -C FORWARD -i "$WAN" -o {wg_iface} -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || '
        f'iptables -I FORWARD 2 -i "$WAN" -o {wg_iface} -m state --state RELATED,ESTABLISHED -j ACCEPT); true'
    )


def wan_wg_forward_down_cmd(wg_iface: str = "wg0") -> str:
    wan = detect_wan_iface_script()
    return (
        f"WAN={wan}; "
        f'[ -n "$WAN" ] && iptables -D FORWARD -i "$WAN" -o {wg_iface} -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true'
    )


def nat_postup(subnet: str, iface_var: str = detect_wan_iface_script()) -> str:
    return (
        f"sysctl -w net.ipv4.ip_forward=1 2>/dev/null || true; "
        f"sysctl -w net.ipv4.conf.%i.rp_filter=0 2>/dev/null || true; "
        f"WAN={iface_var}; "
        f"[ -n \"$WAN\" ] && iptables -t nat -C POSTROUTING -s {subnet} -o \"$WAN\" -j MASQUERADE 2>/dev/null || "
        f"iptables -t nat -A POSTROUTING -s {subnet} -o \"$WAN\" -j MASQUERADE; "
        f"iptables -C FORWARD -i %i -j ACCEPT 2>/dev/null || iptables -A FORWARD -i %i -j ACCEPT; "
        f"iptables -C FORWARD -o %i -j ACCEPT 2>/dev/null || iptables -A FORWARD -o %i -j ACCEPT; "
        f"[ -n \"$WAN\" ] && iptables -C FORWARD -i \"$WAN\" -o %i -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || "
        f"iptables -A FORWARD -i \"$WAN\" -o %i -m state --state RELATED,ESTABLISHED -j ACCEPT"
    )


def nat_postdown(subnet: str, iface_var: str = detect_wan_iface_script()) -> str:
    return (
        f"WAN={iface_var}; "
        f"[ -n \"$WAN\" ] && iptables -t nat -D POSTROUTING -s {subnet} -o \"$WAN\" -j MASQUERADE 2>/dev/null || true; "
        f"iptables -D FORWARD -i %i -j ACCEPT 2>/dev/null || true; "
        f"iptables -D FORWARD -o %i -j ACCEPT 2>/dev/null || true; "
        f"[ -n \"$WAN\" ] && iptables -D FORWARD -i \"$WAN\" -o %i -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true"
    )


def _detect_compose_runtime(runner: RemoteRunner) -> str:
    """Return podman-compose, podman compose, or docker compose — whichever is available."""
    code, _, _ = runner.run("podman compose version >/dev/null 2>&1")
    if code == 0:
        return "podman compose"
    code, _, _ = runner.run("command -v podman-compose >/dev/null 2>&1")
    if code == 0:
        return "podman-compose"
    code, _, _ = runner.run("docker compose version >/dev/null 2>&1")
    if code == 0:
        return "docker compose"
    raise RuntimeError("Podman/Docker Compose недоступен на VPS")


def podman_compose_up(runner: RemoteRunner, workdir: str, project_name: str) -> None:
    ensure_podman(runner)
    workdir_q = shell_path(workdir)
    code, _, _ = runner.run(f"test -d {workdir_q}")
    if code != 0:
        mkdir_remote(runner, workdir)
    project_q = shlex.quote(project_name)
    runtime = _detect_compose_runtime(runner)
    if runtime == "podman compose":
        cmd = f"cd {workdir_q} && podman compose -p {project_q} up -d --remove-orphans"
    elif runtime == "podman-compose":
        cmd = f"cd {workdir_q} && podman-compose -f docker-compose.yml -p {project_q} up -d"
    else:
        cmd = f"cd {workdir_q} && docker compose -p {project_q} up -d --remove-orphans"
    code, out, err = runner.run(cmd, timeout=900)
    if code != 0:
        detail = (err or out or "unknown error").strip()
        raise RuntimeError(f"compose up failed ({runtime}): {detail}")


def podman_compose_down(runner: RemoteRunner, workdir: str, project_name: str, *, timeout: int = 120) -> None:
    workdir_q = shell_path(workdir)
    project_q = shlex.quote(project_name)
    cmd = (
        f"cd {workdir_q} && "
        f"(podman-compose -f docker-compose.yml -p {project_q} down 2>/dev/null || "
        f"podman compose -p {project_q} down 2>/dev/null || "
        f"docker compose -p {project_q} down 2>/dev/null || true)"
    )
    runner.run(cmd, timeout=timeout)


def wait_for_remote_file(runner: RemoteRunner, path: str, attempts: int = 36, delay_sec: int = 5) -> bool:
    path_q = shlex.quote(path)
    for _ in range(attempts):
        code, out, _ = runner.run(f"test -f {path_q} && echo yes")
        if code == 0 and "yes" in out:
            return True
        runner.run(f"sleep {delay_sec}")
    return False
