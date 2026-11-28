from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

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
)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM standalone_proxies")
        conn.execute("DELETE FROM realms_brokers")
        conn.execute("DELETE FROM servers WHERE id IN ('srv-100', 'srv-200')")
        conn.execute(
            "INSERT INTO servers (id, name, host, port, username, auth_type, auth_secret, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("srv-100", "Test VPS 1", "192.0.2.55", 22, "root", "password", "pass", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO servers (id, name, host, port, username, auth_type, auth_secret, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("srv-200", "Test VPS 2", "198.51.100.10", 22, "root", "password", "pass", "2026-01-01T00:00:00Z"),
        )
        conn.commit()


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
