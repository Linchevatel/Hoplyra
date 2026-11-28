from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from hoplyra import db
from hoplyra.db import connect, init_db
from hoplyra.standalone_proxies import (
    SUPPORTED_PROXY_TYPES,
    StandaloneProxyError,
    _find_free_proxy_port,
    _render_compose_file,
    delete_standalone_proxy,
    deploy_standalone_proxy,
    get_standalone_proxy_by_id,
    list_standalone_proxies,
    start_standalone_proxy,
    stop_standalone_proxy,
    wait_for_proxy_deploy_jobs,
)


@pytest.fixture(autouse=True)
def setup_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hoplyra.db")
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO servers (id, name, host, port, username, auth_type, auth_secret, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("srv-100", "Test VPS 1", "192.0.2.55", 22, "root", "password", "pass", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO servers (id, name, host, port, username, auth_type, auth_secret, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("srv-200", "Test VPS 2", "198.51.100.10", 22, "root", "password", "pass", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
    yield
    wait_for_proxy_deploy_jobs(timeout=5)


def fake_server(server_id: str = "srv-100", host: str = "192.0.2.55"):
    return {
        "id": server_id,
        "host": host,
        "port": 22,
        "username": "root",
        "auth_type": "password",
        "auth_secret": "pass",
    }


def fake_runner(host: str = "192.0.2.55"):
    mock = MagicMock()
    mock.target = SimpleNamespace(is_local=False, host=host)
    mock.run.return_value = (0, "", "")
    return mock


# Valid 16-byte key (32 hex chars) + hex("cloudflare.com")
VALID_MTPROTO_SECRET = "ee" + "00112233445566778899aabbccddeeff" + "cloudflare.com".encode("ascii").hex()


@pytest.mark.parametrize("proxy_type", SUPPORTED_PROXY_TYPES)
def test_deploy_standalone_proxies_all_types(proxy_type):
    runner = fake_runner()
    runner.run.side_effect = lambda cmd, **kwargs: (0, "exited\n", "") if " inspect " in cmd else (0, "", "")

    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.ensure_image"),
        patch("hoplyra.standalone_proxies.podman_compose_up", return_value="docker compose") as mock_up,
        patch("hoplyra.standalone_proxies._wait_proxy_ready") as mock_wait,
    ):
        result = deploy_standalone_proxy(
            server_id="srv-100",
            proxy_type=proxy_type,
            name=f"My {proxy_type.upper()}",
            listen_port=9000,
            username="testuser",
            password="testpassword",
            secret=VALID_MTPROTO_SECRET if proxy_type == "mtproto" else None,
            sni="cloudflare.com" if proxy_type == "mtproto" else None,
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
        )

    assert result["proxyType"] == proxy_type
    assert result["status"] == "active"
    assert result["listenPort"] == 9000
    assert result["runtime"] == "docker compose"

    if proxy_type == "socks5":
        assert result["clientUri"] == "socks5://testuser:testpassword@192.0.2.55:9000"
    elif proxy_type == "https":
        assert result["clientUri"] == "https://testuser:testpassword@192.0.2.55:9000"
    elif proxy_type == "shadowsocks":
        assert result["clientUri"].startswith("ss://")
        assert "@192.0.2.55:9000" in result["clientUri"]
    elif proxy_type == "mtproto":
        assert result["clientUri"] == f"tg://proxy?server=192.0.2.55&port=9000&secret={VALID_MTPROTO_SECRET}"

    mock_wait.assert_called_once()
    mock_up.assert_called_once()

    proxies = list_standalone_proxies()
    assert len(proxies) == 1
    assert proxies[0]["id"] == result["id"]

    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.podman_compose_down") as mock_down,
        patch("hoplyra.standalone_proxies.podman_compose_up", return_value="docker compose"),
        patch("hoplyra.standalone_proxies._wait_proxy_ready"),
    ):
        stopped = stop_standalone_proxy(
            result["id"],
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
        )
        assert stopped["status"] == "inactive"
        assert get_standalone_proxy_by_id(result["id"])["status"] == "inactive"
        mock_down.assert_called_once()

        started = start_standalone_proxy(
            result["id"],
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
        )
        assert started["status"] == "active"
        assert get_standalone_proxy_by_id(result["id"])["status"] == "active"

        deleted = delete_standalone_proxy(
            result["id"],
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
        )
        assert deleted["status"] == "deleted"
        assert len(list_standalone_proxies()) == 0


def test_render_compose_file_escapes_yaml_and_dollar_interpolation():
    compose_text = _render_compose_file(
        image="hoplyra-gost:1",
        container_name='name-"$TEST"',
        ports=["1080:1080/tcp"],
        volumes=['/opt/dir$1/"sub":/etc/gost/cert.pem:ro'],
        command=["-L", 'socks5://u$er:p%2Fa%22s%24w@0.0.0.0:1080'],
    )
    parsed = yaml.safe_load(compose_text)
    svc = parsed["services"]["proxy"]
    assert svc["container_name"] == 'name-"$$TEST"'
    assert svc["volumes"] == ['/opt/dir$$1/"sub":/etc/gost/cert.pem:ro']
    assert svc["command"] == ["-L", "socks5://u$$er:p%2Fa%22s%24w@0.0.0.0:1080"]


@pytest.mark.parametrize("proxy_type", ["socks5", "https", "shadowsocks"])
def test_credentials_with_slash_quote_and_dollar_are_encoded_and_yaml_safe(proxy_type):
    runner = fake_runner()
    special_user = 'u$er/"1'
    special_pass = 'a/b"c$d:e@f%g'

    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.ensure_image"),
        patch("hoplyra.standalone_proxies.podman_compose_up", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies._wait_proxy_ready"),
    ):
        result = deploy_standalone_proxy(
            server_id="srv-100",
            proxy_type=proxy_type,
            name='Proxy "special" $1',
            listen_port=9100,
            username=special_user,
            password=special_pass,
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
        )

    uploaded = {call.args[0]: call.args[1] for call in runner.upload_text.call_args_list}
    compose_paths = [p for p in uploaded if p.endswith("/docker-compose.yml")]
    assert len(compose_paths) == 1
    compose_raw = uploaded[compose_paths[0]]
    parsed = yaml.safe_load(compose_raw)
    cmd = parsed["services"]["proxy"]["command"]
    assert cmd[0] == "-L"
    listen_uri = cmd[1]

    # Raw special characters must not appear unencoded in the authority part
    assert "%2F" in listen_uri
    assert "%22" in listen_uri
    assert "%24" in listen_uri
    assert "$" not in listen_uri

    if proxy_type == "socks5":
        assert listen_uri == "socks5://u%24er%2F%221:a%2Fb%22c%24d%3Ae%40f%25g@0.0.0.0:9100"
        assert result["clientUri"] == "socks5://u%24er%2F%221:a%2Fb%22c%24d%3Ae%40f%25g@192.0.2.55:9100"
    elif proxy_type == "https":
        assert listen_uri.startswith("http+tls://u%24er%2F%221:a%2Fb%22c%24d%3Ae%40f%25g@0.0.0.0:9100?")
        assert result["clientUri"] == "https://u%24er%2F%221:a%2Fb%22c%24d%3Ae%40f%25g@192.0.2.55:9100"
    elif proxy_type == "shadowsocks":
        assert listen_uri == "ss://AEAD_CHACHA20_POLY1305:a%2Fb%22c%24d%3Ae%40f%25g@0.0.0.0:9100"
        expected_b64 = base64.urlsafe_b64encode(f"chacha20-ietf-poly1305:{special_pass}".encode()).decode().rstrip("=")
        assert result["clientUri"].startswith(f"ss://{expected_b64}@192.0.2.55:9100#")


@pytest.mark.parametrize(
    "bad_secret",
    [
        "ee123",
        "deadbeef",
        "ee" + "a" * 32,  # missing domain tail
        "ee" + "a" * 31 + "636f6d",  # odd total length
        "ee" + "a" * 32 + "ff",  # non-ASCII domain bytes
        "ee" + "a" * 32 + "202e636f6d",  # space in domain
        "dd" + "a" * 32 + "cloudflare.com".encode().hex(),  # not ee prefix
    ],
)
def test_deploy_mtproto_rejects_invalid_secret(bad_secret):
    with pytest.raises(StandaloneProxyError):
        deploy_standalone_proxy(
            server_id="srv-100",
            proxy_type="mtproto",
            listen_port=8443,
            secret=bad_secret,
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
        )


def test_delete_failure_preserves_directory_and_marks_error_then_allows_retry():
    runner = fake_runner()

    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.ensure_image"),
        patch("hoplyra.standalone_proxies.podman_compose_up", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies._wait_proxy_ready"),
    ):
        created = deploy_standalone_proxy(
            server_id="srv-100",
            proxy_type="socks5",
            listen_port=9200,
            username="u",
            password="p",
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
        )

    runner.run.reset_mock()

    # 1) compose down fails -> rm -rf MUST NOT run, DB row kept with status='error'
    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.podman_compose_down", side_effect=RuntimeError("daemon unreachable")) as mock_down,
    ):
        with pytest.raises(StandaloneProxyError, match="daemon unreachable"):
            delete_standalone_proxy(
                created["id"],
                get_server_row=lambda sid: fake_server(sid),
                server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
            )

    mock_down.assert_called_once()
    assert mock_down.call_args.kwargs["strict"] is True
    assert mock_down.call_args.kwargs["runtime"] == "podman-compose"
    rm_calls = [c for c in runner.run.call_args_list if "rm -rf" in c.args[0]]
    assert rm_calls == []

    persisted = get_standalone_proxy_by_id(created["id"])
    assert persisted["status"] == "error"
    assert "daemon unreachable" in persisted["meta"]["error"]

    # 2) compose down succeeds, inspect confirms exited, rm -rf fails -> DB row still kept with status='error'
    runner.run.reset_mock()
    runner.run.side_effect = lambda cmd, **kwargs: (0, "exited\n", "") if " inspect " in cmd else (1, "", "permission denied")
    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.podman_compose_down"),
    ):
        with pytest.raises(StandaloneProxyError, match="permission denied"):
            delete_standalone_proxy(
                created["id"],
                get_server_row=lambda sid: fake_server(sid),
                server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
            )

    persisted = get_standalone_proxy_by_id(created["id"])
    assert persisted["status"] == "error"

    # 3) Retry succeeds -> directory removed and DB row deleted
    runner.run.reset_mock()
    runner.run.side_effect = lambda cmd, **kwargs: (0, "exited\n", "") if " inspect " in cmd else (0, "", "")
    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.podman_compose_down") as mock_down_ok,
    ):
        res = delete_standalone_proxy(
            created["id"],
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
        )

    assert res == {"status": "deleted"}
    mock_down_ok.assert_called_once()
    assert any("rm -rf" in c.args[0] for c in runner.run.call_args_list)
    with pytest.raises(LookupError):
        get_standalone_proxy_by_id(created["id"])


def test_delete_missing_dir_keeps_proxy_when_container_running_or_inspect_unknown():
    runner = fake_runner()

    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.ensure_image"),
        patch("hoplyra.standalone_proxies.podman_compose_up", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies._wait_proxy_ready"),
    ):
        created = deploy_standalone_proxy(
            server_id="srv-100",
            proxy_type="socks5",
            listen_port=9250,
            username="u",
            password="p",
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
        )

    # Do NOT mock podman_compose_down: simulate real missing directory (test -d -> 1),
    # so podman_compose_down(..., strict=True) returns silently, while podman inspect reports 'running'.
    def run_missing_dir_running(cmd, **kwargs):
        if cmd.startswith("test -d "):
            return (1, "", "")
        if cmd.startswith("podman inspect "):
            return (0, "running\n", "")
        return (0, "", "")

    runner.run.reset_mock()
    runner.run.side_effect = run_missing_dir_running

    with patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner):
        with pytest.raises(StandaloneProxyError, match="not stopped"):
            delete_standalone_proxy(
                created["id"],
                get_server_row=lambda sid: fake_server(sid),
                server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
            )

    assert not any("rm -rf" in c.args[0] for c in runner.run.call_args_list)
    visible = list_standalone_proxies()
    assert len(visible) == 1
    assert visible[0]["id"] == created["id"]
    assert visible[0]["status"] == "error"

    # Unknown inspect result (inspect fails + ps -a fails) also blocks deletion and keeps error row
    def run_missing_dir_unknown(cmd, **kwargs):
        if cmd.startswith("test -d "):
            return (1, "", "")
        if cmd.startswith("podman inspect "):
            return (125, "", "runtime socket unavailable")
        if cmd.startswith("podman ps "):
            return (125, "", "runtime socket unavailable")
        return (0, "", "")

    runner.run.reset_mock()
    runner.run.side_effect = run_missing_dir_unknown

    with patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner):
        with pytest.raises(StandaloneProxyError, match="runtime socket unavailable"):
            delete_standalone_proxy(
                created["id"],
                get_server_row=lambda sid: fake_server(sid),
                server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
            )

    assert len(list_standalone_proxies()) == 1
    assert get_standalone_proxy_by_id(created["id"])["status"] == "error"

    # Once inspect fails with no such object AND ps -a succeeds without container -> DELETE succeeds
    def run_missing_dir_absent(cmd, **kwargs):
        if cmd.startswith("test -d "):
            return (1, "", "")
        if cmd.startswith("podman inspect "):
            return (125, "", 'Error: no such object: "missing"')
        if cmd.startswith("podman ps "):
            return (0, "other_container\n", "")
        return (0, "", "")

    runner.run.reset_mock()
    runner.run.side_effect = run_missing_dir_absent

    with patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner):
        res = delete_standalone_proxy(
            created["id"],
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
        )

    assert res == {"status": "deleted"}
    assert list_standalone_proxies() == []


def test_runtime_is_preserved_on_start_and_rollback():
    runner = fake_runner()

    # Rollback on deploy failure must pass runtime returned by podman_compose_up
    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.ensure_image"),
        patch("hoplyra.standalone_proxies.podman_compose_up", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies._wait_proxy_ready", side_effect=StandaloneProxyError("port not open")),
        patch("hoplyra.standalone_proxies.podman_compose_down") as mock_down,
    ):
        with pytest.raises(StandaloneProxyError, match="port not open"):
            deploy_standalone_proxy(
                server_id="srv-100",
                proxy_type="socks5",
                listen_port=9300,
                username="u",
                password="p",
                get_server_row=lambda sid: fake_server(sid),
                server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
            )

    mock_down.assert_called_once()
    assert mock_down.call_args.kwargs["strict"] is True
    assert mock_down.call_args.kwargs["runtime"] == "podman-compose"

    failed_row = list_standalone_proxies()[0]
    assert failed_row["status"] == "error"
    assert failed_row["runtime"] == "podman-compose"

    # Subsequent start_standalone_proxy must pass saved runtime to podman_compose_up
    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.podman_compose_up", return_value="podman-compose") as mock_up,
        patch("hoplyra.standalone_proxies._wait_proxy_ready") as mock_wait,
    ):
        started = start_standalone_proxy(
            failed_row["id"],
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
        )

    assert started["status"] == "active"
    assert mock_up.call_args.kwargs["runtime"] == "podman-compose"
    assert mock_wait.call_args.kwargs["runtime"] == "podman-compose"


def test_deploy_persists_runtime_before_first_compose_up_and_preserves_on_compose_failure():
    runner = fake_runner()
    seen_db_runtime_during_up: list[str | None] = []

    def compose_up_partial_failure(r, workdir, project, runtime=None):
        rows = list_standalone_proxies()
        seen_db_runtime_during_up.append(rows[0]["runtime"] if rows else None)
        assert runtime == "podman-compose"
        raise RuntimeError("partial stack creation error")

    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.ensure_image"),
        patch("hoplyra.standalone_proxies._detect_compose_runtime", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies.podman_compose_up", side_effect=compose_up_partial_failure),
        patch("hoplyra.standalone_proxies.podman_compose_down") as mock_down,
    ):
        with pytest.raises(StandaloneProxyError, match="partial stack creation error"):
            deploy_standalone_proxy(
                server_id="srv-100",
                proxy_type="socks5",
                listen_port=9350,
                username="u",
                password="p",
                get_server_row=lambda sid: fake_server(sid),
                server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
            )

    assert seen_db_runtime_during_up == ["podman-compose"]
    mock_down.assert_called_once()
    assert mock_down.call_args.kwargs["runtime"] == "podman-compose"

    persisted = list_standalone_proxies()[0]
    assert persisted["status"] == "error"
    assert persisted["runtime"] == "podman-compose"


def test_delete_null_runtime_does_not_trust_empty_docker_when_podman_running():
    runner = fake_runner()
    proxy_id = "legacy-null-rt"
    container_name = f"cv-prx-{proxy_id[:8]}"
    instance_path = f"/opt/hoplyra/proxies/{proxy_id}"

    with connect() as conn:
        conn.execute(
            """INSERT INTO standalone_proxies
               (id, server_id, name, proxy_type, listen_port, status, container_name, instance_path, runtime, client_uri, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, 'error', ?, ?, NULL, 'socks5://1', '{}', '2026-01-01T00:00:00Z')""",
            (proxy_id, "srv-100", "Legacy Proxy", "socks5", 9400, container_name, instance_path),
        )
        conn.commit()

    def run_docker_empty_podman_running(cmd, **kwargs):
        # _detect_compose_runtime selects docker compose
        if "docker info" in cmd and "docker compose version" in cmd:
            return (0, "ok\n", "")
        if cmd.startswith("test -d "):
            return (0, "", "")
        # docker compose down succeeds because Docker has no such stack
        if "docker compose " in cmd and " down" in cmd:
            return (0, "", "")
        # Docker inspect / ps -a report container absent
        if cmd.startswith("docker inspect "):
            return (1, "", "Error: No such object")
        if cmd.startswith("docker ps "):
            return (0, "", "")
        # Podman reports container still running
        if cmd.startswith("podman inspect "):
            return (0, "running\n", "")
        return (0, "", "")

    runner.run.side_effect = run_docker_empty_podman_running

    with patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner):
        with pytest.raises(StandaloneProxyError, match="not stopped in podman"):
            delete_standalone_proxy(
                proxy_id,
                get_server_row=lambda sid: fake_server(sid),
                server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
            )

    assert not any("rm -rf" in c.args[0] for c in runner.run.call_args_list)
    persisted = get_standalone_proxy_by_id(proxy_id)
    assert persisted["status"] == "error"

    # SSH exit 255 on Podman binary probe must NOT treat Podman as absent
    def run_docker_empty_podman_ssh_255(cmd, **kwargs):
        if "docker info" in cmd and "docker compose version" in cmd:
            return (0, "ok\n", "")
        if cmd.startswith("test -d "):
            return (0, "", "")
        if "docker compose " in cmd and " down" in cmd:
            return (0, "", "")
        if cmd.startswith("docker inspect "):
            return (1, "", "Error: No such object")
        if cmd.startswith("docker ps "):
            return (0, "", "")
        if cmd.startswith("podman inspect ") or cmd.startswith("podman ps "):
            return (255, "", "Connection closed by remote host")
        if cmd.startswith("command -v podman"):
            return (255, "", "Connection closed by remote host")
        return (0, "", "")

    runner.run.reset_mock()
    runner.run.side_effect = run_docker_empty_podman_ssh_255

    with patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner):
        with pytest.raises(StandaloneProxyError, match="Connection closed"):
            delete_standalone_proxy(
                proxy_id,
                get_server_row=lambda sid: fake_server(sid),
                server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
            )

    assert not any("rm -rf" in c.args[0] for c in runner.run.call_args_list)
    assert get_standalone_proxy_by_id(proxy_id)["status"] == "error"

    # Truncated/empty stdout (exit 0 without __HOPLYRA_BIN_MISSING__ marker) must also block deletion
    def run_docker_empty_podman_incomplete(cmd, **kwargs):
        if "docker info" in cmd and "docker compose version" in cmd:
            return (0, "ok\n", "")
        if cmd.startswith("test -d "):
            return (0, "", "")
        if "docker compose " in cmd and " down" in cmd:
            return (0, "", "")
        if cmd.startswith("docker inspect "):
            return (1, "", "Error: No such object")
        if cmd.startswith("docker ps "):
            return (0, "", "")
        if cmd.startswith("podman inspect ") or cmd.startswith("podman ps "):
            return (127, "", "podman: command not found")
        if cmd.startswith("command -v podman"):
            return (0, "", "")
        return (0, "", "")

    runner.run.reset_mock()
    runner.run.side_effect = run_docker_empty_podman_incomplete

    with patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner):
        with pytest.raises(StandaloneProxyError, match="command not found"):
            delete_standalone_proxy(
                proxy_id,
                get_server_row=lambda sid: fake_server(sid),
                server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
            )

    assert not any("rm -rf" in c.args[0] for c in runner.run.call_args_list)
    assert get_standalone_proxy_by_id(proxy_id)["status"] == "error"

    # Only explicit remote shell marker __HOPLYRA_BIN_MISSING__ confirms Podman is not installed -> deletion succeeds
    def run_docker_empty_podman_not_installed(cmd, **kwargs):
        if "docker info" in cmd and "docker compose version" in cmd:
            return (0, "ok\n", "")
        if cmd.startswith("test -d "):
            return (0, "", "")
        if "docker compose " in cmd and " down" in cmd:
            return (0, "", "")
        if cmd.startswith("docker inspect "):
            return (1, "", "Error: No such object")
        if cmd.startswith("docker ps "):
            return (0, "", "")
        if cmd.startswith("podman inspect ") or cmd.startswith("podman ps "):
            return (127, "", "podman: command not found")
        if cmd.startswith("command -v podman"):
            return (0, "__HOPLYRA_BIN_MISSING__\n", "")
        return (0, "", "")

    runner.run.reset_mock()
    runner.run.side_effect = run_docker_empty_podman_not_installed

    with patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner):
        res = delete_standalone_proxy(
            proxy_id,
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
        )

    assert res == {"status": "deleted"}
    assert any("rm -rf" in c.args[0] for c in runner.run.call_args_list)
    assert list_standalone_proxies() == []


def test_start_null_runtime_picks_existing_podman_owner_and_leaves_db_on_ambiguity_or_error():
    runner = fake_runner()
    proxy_id = "legacy-start-rt"
    container_name = f"cv-prx-{proxy_id[:8]}"
    instance_path = f"/opt/hoplyra/proxies/{proxy_id}"

    with connect() as conn:
        conn.execute(
            """INSERT INTO standalone_proxies
               (id, server_id, name, proxy_type, listen_port, status, container_name, instance_path, runtime, client_uri, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, 'inactive', ?, ?, NULL, 'socks5://1', '{}', '2026-01-01T00:00:00Z')""",
            (proxy_id, "srv-100", "Legacy Start", "socks5", 9410, container_name, instance_path),
        )
        conn.commit()

    # 1) Ambiguous owner (container exists in BOTH docker and podman) -> raises, does NOT touch DB or call compose_up
    def run_both_engines_have_container(cmd, **kwargs):
        if cmd.startswith("docker inspect "):
            return (0, "exited\n", "")
        if cmd.startswith("podman inspect "):
            return (0, "exited\n", "")
        return (0, "", "")

    runner.run.side_effect = run_both_engines_have_container
    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.podman_compose_up") as mock_up,
    ):
        with pytest.raises(StandaloneProxyError, match="Ambiguous proxy runtime"):
            start_standalone_proxy(
                proxy_id,
                get_server_row=lambda sid: fake_server(sid),
                server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
            )
    mock_up.assert_not_called()
    row_after_ambig = get_standalone_proxy_by_id(proxy_id)
    assert row_after_ambig["runtime"] is None
    assert row_after_ambig["status"] == "inactive"

    # 2) Probe error (SSH 255 on podman) -> raises, does NOT touch DB or call compose_up
    def run_podman_ssh_255(cmd, **kwargs):
        if cmd.startswith("docker inspect "):
            return (1, "", "Error: No such object")
        if cmd.startswith("docker ps "):
            return (0, "", "")
        if cmd.startswith("podman inspect ") or cmd.startswith("podman ps ") or cmd.startswith("command -v podman"):
            return (255, "", "ssh connection lost")
        return (0, "", "")

    runner.run.reset_mock()
    runner.run.side_effect = run_podman_ssh_255
    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.podman_compose_up") as mock_up,
    ):
        with pytest.raises(StandaloneProxyError, match="ssh connection lost"):
            start_standalone_proxy(
                proxy_id,
                get_server_row=lambda sid: fake_server(sid),
                server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
            )
    mock_up.assert_not_called()
    row_after_err = get_standalone_proxy_by_id(proxy_id)
    assert row_after_err["runtime"] is None
    assert row_after_err["status"] == "inactive"

    # 3) Container exists in Podman, Docker Compose is ALSO available -> MUST bind Podman (not Docker)
    def run_podman_owns_container_docker_compose_also_available(cmd, **kwargs):
        if "docker info" in cmd and "docker compose version" in cmd:
            return (0, "ok\n", "")
        if cmd.startswith("docker inspect "):
            return (1, "", "Error: No such object")
        if cmd.startswith("docker ps "):
            return (0, "", "")
        if cmd.startswith("podman inspect "):
            return (0, "exited\n", "")
        if cmd.startswith("podman compose version"):
            return (1, "", "not installed")
        if cmd.startswith("command -v podman-compose"):
            return (0, "", "")
        return (0, "", "")

    runner.run.reset_mock()
    runner.run.side_effect = run_podman_owns_container_docker_compose_also_available
    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.podman_compose_up", return_value="podman-compose") as mock_up,
        patch("hoplyra.standalone_proxies._wait_proxy_ready"),
    ):
        res = start_standalone_proxy(
            proxy_id,
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda r: SimpleNamespace(host=r["host"], is_local=False),
        )

    assert res == {"status": "active"}
    assert mock_up.call_args.kwargs["runtime"] == "podman-compose"
    row_final = get_standalone_proxy_by_id(proxy_id)
    assert row_final["runtime"] == "podman-compose"
    assert row_final["status"] == "active"


def test_find_free_proxy_port_scopes_by_server_and_fails_on_ss_or_exhaustion():
    runner = fake_runner()
    runner.run.return_value = (0, "", "")

    with connect() as conn:
        # Active proxy on srv-100 occupies 1080; realms broker on srv-100 occupies 1081
        conn.execute(
            """INSERT INTO standalone_proxies
               (id, server_id, name, proxy_type, listen_port, status, container_name, instance_path, client_uri, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("prx-1", "srv-100", "S1", "socks5", 1080, "active", "c1", "/opt/1", "socks5://1", "{}", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            """INSERT INTO realms_brokers
               (id, server_id, listen_port, token, domain_or_ip, status, container_name, instance_path, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("brk-1", "srv-100", 1081, "tok", "example.com", "deploying", "b1", "/opt/b1", "{}", "2026-01-01T00:00:00Z"),
        )
        conn.commit()

    # srv-100 skips 1080 & 1081 -> returns 1082
    assert _find_free_proxy_port(runner, "srv-100", 1080, "socks5") == 1082
    # srv-200 is unaffected by srv-100 -> gets 1080
    assert _find_free_proxy_port(runner, "srv-200", 1080, "socks5") == 1080

    # ss failure must raise StandaloneProxyError
    runner.run.return_value = (1, "", "ss: command not found")
    with pytest.raises(StandaloneProxyError, match="Failed to check listening ports"):
        _find_free_proxy_port(runner, "srv-200", 1080, "socks5")

    # Exhausted port range must raise StandaloneProxyError instead of returning default_port
    runner.run.return_value = (0, "LISTEN 0 128 0.0.0.0:65534 0.0.0.0:*\nLISTEN 0 128 0.0.0.0:65535 0.0.0.0:*\n", "")
    with pytest.raises(StandaloneProxyError, match="No free port available"):
        _find_free_proxy_port(runner, "srv-200", 65534, "socks5")


def test_api_standalone_proxies_lifecycle_and_404():
    from fastapi.testclient import TestClient
    from hoplyra.main import app

    with connect() as conn:
        conn.execute("DELETE FROM servers WHERE id = 'srv-api'")
        conn.execute(
            "INSERT INTO servers (id, name, host, port, username, auth_type, auth_secret, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("srv-api", "API VPS", "192.0.2.88", 22, "root", "agent", None, "2026-01-01T00:00:00Z"),
        )
        conn.commit()

    client = TestClient(app)
    runner = fake_runner("192.0.2.88")

    def _side_effect_run(cmd, timeout=30):
        if "docker ps -a" in cmd or "podman ps -a" in cmd:
            return (0, "", "")
        if "docker inspect" in cmd or "podman inspect" in cmd:
            return (1, "", "no such container")
        return (0, "", "")

    runner.run.side_effect = _side_effect_run

    with (
        patch("hoplyra.auth.is_authenticated", return_value=True),
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.ensure_podman"),
        patch("hoplyra.standalone_proxies.ensure_image"),
        patch("hoplyra.standalone_proxies._detect_compose_runtime", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies.mkdir_remote"),
        patch("hoplyra.standalone_proxies.podman_compose_up", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies.podman_compose_down"),
        patch("hoplyra.standalone_proxies._wait_proxy_ready"),
    ):
        # Missing server should cleanly return 404 (not NameError / 500)
        missing_srv = client.post(
            "/api/standalone-proxies",
            json={"serverId": "no-such-server", "proxyType": "socks5"},
        )
        assert missing_srv.status_code == 404

        created = client.post(
            "/api/standalone-proxies",
            json={"serverId": "srv-api", "proxyType": "socks5", "name": "API Proxy"},
        )
        assert created.status_code == 200, created.text
        proxy_id = created.json()["id"]

        stopped = client.post(f"/api/standalone-proxies/{proxy_id}/stop")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["status"] == "inactive"

        started = client.post(f"/api/standalone-proxies/{proxy_id}/start")
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "active"

        deleted = client.delete(f"/api/standalone-proxies/{proxy_id}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"status": "deleted"}

        # Verify deleted proxy returns 404
        assert client.get(f"/api/standalone-proxies/{proxy_id}").status_code == 404
        assert client.post(f"/api/standalone-proxies/{proxy_id}/stop").status_code == 404
        assert client.delete(f"/api/standalone-proxies/{proxy_id}").status_code == 404

        # Verify StandaloneProxyError on stop/start/delete maps to 400 (not 500)
        with patch("hoplyra.standalone_proxies.stop_standalone_proxy", side_effect=StandaloneProxyError("runtime busy")):
            assert client.post("/api/standalone-proxies/any/stop").status_code == 400


def test_api_standalone_proxy_async_deploy_populates_deploy_log():
    import threading
    import time
    from fastapi.testclient import TestClient
    from hoplyra import deploy_log
    from hoplyra.main import app

    with connect() as conn:
        conn.execute("DELETE FROM servers WHERE id = 'srv-async'")
        conn.execute(
            "INSERT INTO servers (id, name, host, port, username, auth_type, auth_secret, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("srv-async", "Async VPS", "192.0.2.99", 22, "root", "agent", None, "2026-01-01T00:00:00Z"),
        )
        conn.commit()

    client = TestClient(app)
    runner = fake_runner("192.0.2.99")
    compose_gate = threading.Event()
    compose_started = threading.Event()

    def _gated_compose_up(*args, **kwargs):
        compose_started.set()
        assert compose_gate.wait(timeout=5)
        return "podman-compose"

    with (
        patch("hoplyra.auth.is_authenticated", return_value=True),
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.ensure_podman"),
        patch("hoplyra.standalone_proxies.ensure_image"),
        patch("hoplyra.standalone_proxies._detect_compose_runtime", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies.mkdir_remote"),
        patch("hoplyra.standalone_proxies.podman_compose_up", side_effect=_gated_compose_up),
        patch("hoplyra.standalone_proxies.podman_compose_down"),
        patch("hoplyra.standalone_proxies._wait_proxy_ready"),
    ):
        resp = client.post(
            "/api/standalone-proxies",
            json={
                "serverId": "srv-async",
                "proxyType": "socks5",
                "name": "Async SOCKS",
                "listenPort": 1088,
                "asyncDeploy": True,
            },
        )
        assert resp.status_code == 200, resp.text
        created = resp.json()
        proxy_id = created["id"]
        assert created["status"] == "deploying"
        assert created["listenPort"] == 1088

        assert compose_started.wait(timeout=5)
        mid_log = client.get(f"/api/configs/{proxy_id}/deploy-log")
        assert mid_log.status_code == 200, mid_log.text
        mid_stages = {e["stage"] for e in mid_log.json()["events"] if e.get("stage")}
        assert "init" in mid_stages
        assert "container" in mid_stages

        compose_gate.set()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if deploy_log.status(proxy_id)["finished"]:
                break
            time.sleep(0.05)

        assert deploy_log.status(proxy_id)["finished"] is True
        assert deploy_log.status(proxy_id)["ok"] is True

        final_proxy = client.get(f"/api/standalone-proxies/{proxy_id}")
        assert final_proxy.status_code == 200
        assert final_proxy.json()["status"] == "active"
        assert final_proxy.json()["clientUri"].startswith("socks5://")

        final_log = client.get(f"/api/configs/{proxy_id}/deploy-log")
        assert final_log.status_code == 200
        payload = final_log.json()
        assert payload["status"]["finished"] is True
        assert payload["status"]["ok"] is True
        stages = {e["stage"] for e in payload["events"] if e.get("stage")}
        assert {"init", "port", "config", "image", "container", "ready", "finish"}.issubset(stages)

    # Error path in asyncDeploy writes error status and finishes deploy_log with ok=False
    with (
        patch("hoplyra.auth.is_authenticated", return_value=True),
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.ensure_podman"),
        patch("hoplyra.standalone_proxies.ensure_image"),
        patch("hoplyra.standalone_proxies._detect_compose_runtime", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies.mkdir_remote"),
        patch("hoplyra.standalone_proxies.podman_compose_up", side_effect=RuntimeError("compose start boom")),
        patch("hoplyra.standalone_proxies.podman_compose_down"),
    ):
        err_resp = client.post(
            "/api/standalone-proxies",
            json={"serverId": "srv-async", "proxyType": "socks5", "listenPort": 1089, "asyncDeploy": True},
        )
        assert err_resp.status_code == 200
        err_id = err_resp.json()["id"]
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if deploy_log.status(err_id)["finished"]:
                break
            time.sleep(0.05)

        assert deploy_log.status(err_id)["finished"] is True
        assert deploy_log.status(err_id)["ok"] is False
        failed_row = client.get(f"/api/standalone-proxies/{err_id}").json()
        assert failed_row["status"] == "error"
        assert "compose start boom" in failed_row["meta"]["error"]


def test_api_stop_start_delete_return_409_during_async_deploy_and_cannot_race():
    import threading
    import time
    from fastapi.testclient import TestClient
    from hoplyra import deploy_log
    from hoplyra.main import app

    with connect() as conn:
        conn.execute("DELETE FROM servers WHERE id = 'srv-race'")
        conn.execute(
            "INSERT INTO servers (id, name, host, port, username, auth_type, auth_secret, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("srv-race", "Race VPS", "192.0.2.101", 22, "root", "agent", None, "2026-01-01T00:00:00Z"),
        )
        conn.commit()

    client = TestClient(app)
    runner = fake_runner("192.0.2.101")
    runner.run.side_effect = lambda cmd, **kwargs: (0, "exited\n", "") if " inspect " in cmd else (0, "", "")

    compose_entered = threading.Event()
    compose_release = threading.Event()

    def _paused_compose_up(*args, **kwargs):
        compose_entered.set()
        assert compose_release.wait(timeout=5)
        return "podman-compose"

    with (
        patch("hoplyra.auth.is_authenticated", return_value=True),
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.ensure_podman"),
        patch("hoplyra.standalone_proxies.ensure_image"),
        patch("hoplyra.standalone_proxies._detect_compose_runtime", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies.mkdir_remote"),
        patch("hoplyra.standalone_proxies.podman_compose_up", side_effect=_paused_compose_up),
        patch("hoplyra.standalone_proxies.podman_compose_down") as mock_down,
        patch("hoplyra.standalone_proxies._wait_proxy_ready"),
    ):
        resp = client.post(
            "/api/standalone-proxies",
            json={"serverId": "srv-race", "proxyType": "socks5", "name": "Race SOCKS", "asyncDeploy": True},
        )
        assert resp.status_code == 200, resp.text
        proxy_id = resp.json()["id"]

        assert compose_entered.wait(timeout=5)

        # While deploy worker is paused before compose up completes, stop/start/delete MUST return 409
        stop_during = client.post(f"/api/standalone-proxies/{proxy_id}/stop")
        start_during = client.post(f"/api/standalone-proxies/{proxy_id}/start")
        del_during = client.delete(f"/api/standalone-proxies/{proxy_id}")

        assert stop_during.status_code == 409, stop_during.text
        assert start_during.status_code == 409, start_during.text
        assert del_during.status_code == 409, del_during.text
        mock_down.assert_not_called()
        assert get_standalone_proxy_by_id(proxy_id)["status"] == "deploying"

        # Let deploy finish cleanly
        compose_release.set()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if deploy_log.status(proxy_id)["finished"]:
                break
            time.sleep(0.05)

        assert get_standalone_proxy_by_id(proxy_id)["status"] == "active"

        # Now stop succeeds and cannot be overwritten back to active
        stop_after = client.post(f"/api/standalone-proxies/{proxy_id}/stop")
        assert stop_after.status_code == 200, stop_after.text
        assert stop_after.json() == {"status": "inactive"}
        mock_down.assert_called_once()
        assert get_standalone_proxy_by_id(proxy_id)["status"] == "inactive"


def test_first_proxy_on_empty_vps_picks_default_port_and_rejects_duplicate_explicit_port():
    import time
    from fastapi.testclient import TestClient
    from hoplyra import deploy_log
    from hoplyra.main import app

    with connect() as conn:
        conn.execute("DELETE FROM servers WHERE id = 'srv-empty'")
        conn.execute(
            "INSERT INTO servers (id, name, host, port, username, auth_type, auth_secret, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("srv-empty", "Empty VPS", "192.0.2.102", 22, "root", "agent", None, "2026-01-01T00:00:00Z"),
        )
        conn.commit()

    client = TestClient(app)
    runner = fake_runner("192.0.2.102")
    runner.run.return_value = (0, "", "")

    with (
        patch("hoplyra.auth.is_authenticated", return_value=True),
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.ensure_podman"),
        patch("hoplyra.standalone_proxies.ensure_image"),
        patch("hoplyra.standalone_proxies._detect_compose_runtime", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies.mkdir_remote"),
        patch("hoplyra.standalone_proxies.podman_compose_up", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies._wait_proxy_ready"),
    ):
        resp = client.post(
            "/api/standalone-proxies",
            json={"serverId": "srv-empty", "proxyType": "socks5", "asyncDeploy": True},
        )
        assert resp.status_code == 200, resp.text
        proxy_id = resp.json()["id"]

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if deploy_log.status(proxy_id)["finished"]:
                break
            time.sleep(0.05)

        first = client.get(f"/api/standalone-proxies/{proxy_id}").json()
        assert first["status"] == "active"
        assert first["listenPort"] == 1080
        assert ":1080" in first["clientUri"]

        # Duplicate explicit port on same server must be rejected cleanly
        dup = client.post(
            "/api/standalone-proxies",
            json={"serverId": "srv-empty", "proxyType": "socks5", "listenPort": 1080},
        )
        assert dup.status_code == 400


def test_concurrent_async_deploys_reserve_distinct_ports():
    import threading
    import time
    from fastapi.testclient import TestClient
    from hoplyra import deploy_log
    from hoplyra.main import app

    with connect() as conn:
        conn.execute("DELETE FROM servers WHERE id = 'srv-par'")
        conn.execute(
            "INSERT INTO servers (id, name, host, port, username, auth_type, auth_secret, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("srv-par", "Parallel VPS", "192.0.2.103", 22, "root", "agent", None, "2026-01-01T00:00:00Z"),
        )
        conn.commit()

    client = TestClient(app)
    runner = fake_runner("192.0.2.103")
    scan_barrier = threading.Barrier(2, timeout=5)

    def _run_with_barrier(cmd, **kwargs):
        if cmd.startswith("ss -H -lntu"):
            scan_barrier.wait()
            return (0, "", "")
        return (0, "", "")

    runner.run.side_effect = _run_with_barrier

    with (
        patch("hoplyra.auth.is_authenticated", return_value=True),
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.ensure_podman"),
        patch("hoplyra.standalone_proxies.ensure_image"),
        patch("hoplyra.standalone_proxies._detect_compose_runtime", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies.mkdir_remote"),
        patch("hoplyra.standalone_proxies.podman_compose_up", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies._wait_proxy_ready"),
    ):
        r1 = client.post(
            "/api/standalone-proxies",
            json={"serverId": "srv-par", "proxyType": "socks5", "name": "Par 1", "asyncDeploy": True},
        )
        r2 = client.post(
            "/api/standalone-proxies",
            json={"serverId": "srv-par", "proxyType": "socks5", "name": "Par 2", "asyncDeploy": True},
        )
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        id1, id2 = r1.json()["id"], r2.json()["id"]

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if deploy_log.status(id1)["finished"] and deploy_log.status(id2)["finished"]:
                break
            time.sleep(0.05)

        p1 = client.get(f"/api/standalone-proxies/{id1}").json()
        p2 = client.get(f"/api/standalone-proxies/{id2}").json()
        assert p1["status"] == "active"
        assert p2["status"] == "active"
        assert {p1["listenPort"], p2["listenPort"]} == {1080, 1081}


def test_recover_stuck_standalone_proxies_restores_running_or_marks_error():
    from hoplyra.standalone_proxies import (
        _register_proxy_deploy,
        _unregister_proxy_deploy,
        recover_stuck_standalone_proxies,
    )

    with connect() as conn:
        # 1) Stuck with runtime + running container -> should recover to active
        conn.execute(
            """INSERT INTO standalone_proxies
               (id, server_id, name, proxy_type, listen_port, status, container_name, instance_path, runtime, client_uri, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, 'deploying', ?, ?, ?, ?, ?, ?)""",
            (
                "prx-rec-run",
                "srv-100",
                "Recovered Running",
                "socks5",
                1080,
                "cv-prx-recrun01",
                "/opt/hoplyra/proxies/prx-rec-run",
                "podman-compose",
                "socks5://u:p@192.0.2.55:1080",
                json.dumps({"serverHost": "192.0.2.55", "listenPort": 1080, "runtime": "podman-compose"}),
                "2026-01-01T00:00:00Z",
            ),
        )
        # 2) Stuck with runtime + exited container -> should become error (retryable via start)
        conn.execute(
            """INSERT INTO standalone_proxies
               (id, server_id, name, proxy_type, listen_port, status, container_name, instance_path, runtime, client_uri, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, 'deploying', ?, ?, ?, ?, ?, ?)""",
            (
                "prx-rec-down",
                "srv-100",
                "Recovered Exited",
                "socks5",
                1081,
                "cv-prx-recdwn2",
                "/opt/hoplyra/proxies/prx-rec-down",
                "podman-compose",
                "socks5://u:p@192.0.2.55:1081",
                json.dumps({"serverHost": "192.0.2.55", "listenPort": 1081, "runtime": "podman-compose"}),
                "2026-01-01T00:00:00Z",
            ),
        )
        # 3) Stuck early before compose (runtime=NULL, listen_port=0, client_uri='') -> should become error (deletable)
        conn.execute(
            """INSERT INTO standalone_proxies
               (id, server_id, name, proxy_type, listen_port, status, container_name, instance_path, runtime, client_uri, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, 'deploying', ?, ?, NULL, '', ?, ?)""",
            (
                "prx-rec-early",
                "srv-100",
                "Recovered Early",
                "socks5",
                0,
                "cv-prx-recearly",
                "/opt/hoplyra/proxies/prx-rec-early",
                json.dumps({"serverHost": "192.0.2.55", "listenPort": 0}),
                "2026-01-01T00:00:00Z",
            ),
        )
        # 4) Actively running in this process -> must be skipped by recovery
        conn.execute(
            """INSERT INTO standalone_proxies
               (id, server_id, name, proxy_type, listen_port, status, container_name, instance_path, runtime, client_uri, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, 'deploying', ?, ?, NULL, '', ?, ?)""",
            (
                "prx-rec-live",
                "srv-100",
                "Live Deploy",
                "socks5",
                0,
                "cv-prx-reclive1",
                "/opt/hoplyra/proxies/prx-rec-live",
                "{}",
                "2026-01-01T00:00:00Z",
            ),
        )
        conn.commit()

    runner = fake_runner()

    def _probe_run(cmd, **kwargs):
        if "inspect" in cmd and "cv-prx-recrun01" in cmd:
            return (0, "running\n", "")
        if "inspect" in cmd and "cv-prx-recdwn2" in cmd:
            return (0, "exited\n", "")
        if "inspect" in cmd and "cv-prx-recearly" in cmd:
            return (1, "", "no such object")
        return (0, "", "")

    runner.run.side_effect = _probe_run

    _register_proxy_deploy("prx-rec-live")
    try:
        with (
            patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
            patch("hoplyra.standalone_proxies._wait_proxy_ready"),
        ):
            outcomes = recover_stuck_standalone_proxies(
                get_server_row=lambda sid: fake_server(sid),
                server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
            )
    finally:
        _unregister_proxy_deploy("prx-rec-live")

    assert outcomes == {
        "prx-rec-run": "active",
        "prx-rec-down": "error",
        "prx-rec-early": "error",
    }
    assert get_standalone_proxy_by_id("prx-rec-run")["status"] == "active"
    assert get_standalone_proxy_by_id("prx-rec-down")["status"] == "error"
    assert "interrupted by service restart" in get_standalone_proxy_by_id("prx-rec-down")["meta"]["error"]
    assert get_standalone_proxy_by_id("prx-rec-early")["status"] == "error"
    assert get_standalone_proxy_by_id("prx-rec-live")["status"] == "deploying"

    # Retry start on prx-rec-down works cleanly
    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.podman_compose_up", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies._wait_proxy_ready"),
    ):
        restarted = start_standalone_proxy(
            "prx-rec-down",
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
        )
    assert restarted == {"status": "active"}

    # Early-interrupted record deletes cleanly without calling compose down
    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.podman_compose_down") as mock_down_early,
    ):
        deleted = delete_standalone_proxy(
            "prx-rec-early",
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
        )
    assert deleted == {"status": "deleted"}
    mock_down_early.assert_not_called()


def test_shutdown_waits_for_paused_proxy_recovery_before_finishing():
    import threading
    import time
    from hoplyra import main
    from hoplyra.standalone_proxies import schedule_recover_stuck_standalone_proxies

    with connect() as conn:
        conn.execute(
            """INSERT INTO standalone_proxies
               (id, server_id, name, proxy_type, listen_port, status, container_name, instance_path, runtime, client_uri, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, 'deploying', ?, ?, ?, ?, ?, ?)""",
            (
                "prx-shutdown-rec",
                "srv-100",
                "Shutdown Recovery",
                "socks5",
                1085,
                "cv-prx-shutrec1",
                "/opt/hoplyra/proxies/prx-shutdown-rec",
                "podman-compose",
                "socks5://u:p@192.0.2.55:1085",
                json.dumps({"serverHost": "192.0.2.55", "listenPort": 1085, "runtime": "podman-compose"}),
                "2026-01-01T00:00:00Z",
            ),
        )
        conn.commit()

    probe_entered = threading.Event()
    probe_release = threading.Event()
    shutdown_done = threading.Event()
    runner = fake_runner()

    def _paused_probe(cmd, **kwargs):
        if "inspect" in cmd and "cv-prx-shutrec1" in cmd:
            probe_entered.set()
            assert probe_release.wait(timeout=5)
            return (0, "running\n", "")
        return (0, "", "")

    runner.run.side_effect = _paused_probe

    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies._wait_proxy_ready"),
        patch.object(main, "wait_for_deploy_jobs"),
        patch.object(main.metrics_cache, "stop"),
    ):
        rec_thread = schedule_recover_stuck_standalone_proxies(
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
        )
        assert rec_thread is not None
        assert probe_entered.wait(timeout=5)

        def _run_shutdown() -> None:
            main.shutdown()
            shutdown_done.set()

        shut_thread = threading.Thread(target=_run_shutdown)
        shut_thread.start()
        time.sleep(0.15)
        assert not shutdown_done.is_set()
        assert get_standalone_proxy_by_id("prx-shutdown-rec")["status"] == "deploying"

        probe_release.set()
        assert shutdown_done.wait(timeout=5)
        shut_thread.join(timeout=5)

    assert get_standalone_proxy_by_id("prx-shutdown-rec")["status"] == "active"


def test_proxy_lock_registry_does_not_leak_for_unknown_or_completed_ids():
    from fastapi.testclient import TestClient
    from hoplyra.main import app
    from hoplyra.standalone_proxies import _PROXY_LOCKS

    assert len(_PROXY_LOCKS) == 0
    client = TestClient(app)
    runner = fake_runner()
    runner.run.side_effect = lambda cmd, **kwargs: (0, "exited\n", "") if " inspect " in cmd else (0, "", "")

    with patch("hoplyra.auth.is_authenticated", return_value=True):
        for i in range(15):
            assert client.post(f"/api/standalone-proxies/unknown-proxy-{i}/stop").status_code == 404
            assert client.post(f"/api/standalone-proxies/unknown-proxy-{i}/start").status_code == 404
            assert client.delete(f"/api/standalone-proxies/unknown-proxy-{i}").status_code == 404

    assert len(_PROXY_LOCKS) == 0

    with (
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies.ensure_image"),
        patch("hoplyra.standalone_proxies.podman_compose_up", return_value="podman-compose"),
        patch("hoplyra.standalone_proxies.podman_compose_down"),
        patch("hoplyra.standalone_proxies._wait_proxy_ready"),
    ):
        created = deploy_standalone_proxy(
            server_id="srv-100",
            proxy_type="socks5",
            listen_port=1095,
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
        )
        assert len(_PROXY_LOCKS) == 0
        stop_standalone_proxy(
            created["id"],
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
        )
        assert len(_PROXY_LOCKS) == 0
        delete_standalone_proxy(
            created["id"],
            get_server_row=lambda sid: fake_server(sid),
            server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
        )
        assert len(_PROXY_LOCKS) == 0


def test_shutdown_waits_even_when_pausing_before_worker_thread_start():
    import threading
    import time
    from hoplyra import main
    from hoplyra.standalone_proxies import schedule_recover_stuck_standalone_proxies

    with connect() as conn:
        conn.execute(
            """INSERT INTO standalone_proxies
               (id, server_id, name, proxy_type, listen_port, status, container_name, instance_path, runtime, client_uri, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, 'deploying', ?, ?, ?, ?, ?, ?)""",
            (
                "prx-start-race",
                "srv-100",
                "Pre-Start Race",
                "socks5",
                1086,
                "cv-prx-startrac",
                "/opt/hoplyra/proxies/prx-start-race",
                "podman-compose",
                "socks5://u:p@192.0.2.55:1086",
                json.dumps({"serverHost": "192.0.2.55", "listenPort": 1086, "runtime": "podman-compose"}),
                "2026-01-01T00:00:00Z",
            ),
        )
        conn.commit()

    before_start = threading.Event()
    allow_start = threading.Event()
    shutdown_done = threading.Event()
    runner = fake_runner()
    runner.run.side_effect = lambda cmd, **kwargs: (0, "running\n", "") if "inspect" in cmd else (0, "", "")

    orig_start = threading.Thread.start

    def _paused_start(self: threading.Thread, *args, **kwargs):
        if self.name == "hoplyra-proxy-recover":
            before_start.set()
            assert allow_start.wait(timeout=5)
        return orig_start(self, *args, **kwargs)

    with (
        patch.object(threading.Thread, "start", _paused_start),
        patch("hoplyra.standalone_proxies.RemoteRunner", return_value=runner),
        patch("hoplyra.standalone_proxies._wait_proxy_ready"),
        patch.object(main, "wait_for_deploy_jobs"),
        patch.object(main.metrics_cache, "stop"),
    ):
        sched_thread = threading.Thread(
            target=lambda: schedule_recover_stuck_standalone_proxies(
                get_server_row=lambda sid: fake_server(sid),
                server_target=lambda row: SimpleNamespace(host=row["host"], port=22, is_local=False),
            )
        )
        sched_thread.start()
        assert before_start.wait(timeout=5)

        shut_thread = threading.Thread(target=lambda: (main.shutdown(), shutdown_done.set()))
        shut_thread.start()
        time.sleep(0.15)
        assert not shutdown_done.is_set()
        assert get_standalone_proxy_by_id("prx-start-race")["status"] == "deploying"

        allow_start.set()
        sched_thread.join(timeout=5)
        assert shutdown_done.wait(timeout=5)
        shut_thread.join(timeout=5)

    assert get_standalone_proxy_by_id("prx-start-race")["status"] == "active"
