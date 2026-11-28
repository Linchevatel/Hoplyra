import json
import shlex
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hoplyra.chains.deployer import ChainDeployer
from hoplyra.chains.planner import ExitEndpoint, HopSpec, build_plan, validate_chain
from hoplyra.image_catalog import all_images
from hoplyra.peers import (
    _create_shadowquic_peer,
    _remove_shadowquic_peer,
    _update_shadowquic_users,
    get_peer_stats,
)
from hoplyra.protocols import (
    ShadowQuicDeployer,
    find_shadowquic_port,
    get_deployer,
    get_deployer_for_config,
    shadowquic_bundle,
)
from hoplyra.resource_allocation import DEFAULT_PORTS


def runner():
    mock = MagicMock()
    mock.target = SimpleNamespace(is_local=False, host="192.0.2.1")
    mock.run.return_value = (0, "", "")
    return mock


def test_deploy_generates_server_client_and_udp_compose():
    remote = runner()
    with patch("hoplyra.protocols.ensure_image") as image, patch("hoplyra.protocols.podman_compose_up") as up:
        result = ShadowQuicDeployer().deploy(remote, "12345678-abcd", "2001:db8::1", resources={"listenPort": 444})
    files = {call.args[0].split("/")[-1]: call.args[1] for call in remote.upload_text.call_args_list}
    assert 'bind-addr: "0.0.0.0:444"' in files["config.yaml"]
    assert "type: shadowquic" in files["config.yaml"]
    assert "jls-upstream:" in files["config.yaml"]
    assert result.meta["password"] in files["config.yaml"]
    assert '"username": "primary"' in files["config.yaml"]
    assert '"444:444/udp"' in files["docker-compose.yml"]
    assert "hoplyra-shadowquic:1" in files["docker-compose.yml"]
    assert f"sq://primary:{result.meta['password']}@[2001:db8::1]:444?" in result.client_config
    assert "sni=cloudflare.com" in result.client_config
    assert "type: shadowquic" in result.client_config
    assert 'server-name: "cloudflare.com"' in result.client_config
    assert result.container_name == "cv-sq-12345678"
    assert len(result.meta["password"]) >= 22
    image.assert_called_once_with(remote, "hoplyra-shadowquic:1", "shadowquic")
    up.assert_called_once_with(remote, result.instance_path, "cv-shadowquic-12345678")
    assert isinstance(get_deployer("shadowquic"), ShadowQuicDeployer)
    assert isinstance(get_deployer_for_config("shadowquic", result.meta), ShadowQuicDeployer)
    assert ("hoplyra-shadowquic:1", "shadowquic") in all_images()
    assert DEFAULT_PORTS["shadowquic"] == 443


def test_stop_stops_compose_project():
    remote = runner()
    with patch("hoplyra.protocols.podman_compose_down") as down:
        ShadowQuicDeployer().stop(remote, "12345678-abcd", instance_path="/opt/hoplyra/instances/12345678-abcd", runtime="podman compose")
    down.assert_called_once_with(remote, "/opt/hoplyra/instances/12345678-abcd", "cv-shadowquic-12345678", strict=True, runtime="podman compose")


def test_port_search_checks_udp_and_tcp():
    remote = runner()
    remote.run.return_value = (0, "udp UNCONN 0 0 *:443 *:*\ntcp LISTEN 0 128 [::]:444 [::]:*\n", "")
    assert find_shadowquic_port(remote) == 445


def test_port_search_fails_on_runner_error():
    remote = runner()
    remote.run.return_value = (1, "", "command not found: ss")
    with pytest.raises(RuntimeError, match="Не удалось получить список занятых портов"):
        find_shadowquic_port(remote)


@pytest.mark.parametrize("tor", [False, True])
def test_chain_entry_uses_socks_bridge_and_blocks_tor_udp(tor):
    remote = runner()
    hop = HopSpec("h", "shadowquic", "s", "192.0.2.1", 0)
    client, services, files, meta = ChainDeployer("/tmp")._shadowquic_entry_bundle(
        remote,
        "/tmp/instance",
        "12345678",
        hop,
        ExitEndpoint("127.0.0.1", 9050, direct=True) if tor else ExitEndpoint("192.0.2.2", 10808),
        tor,
    )
    data = {name: content for name, content, mode in files}
    bridge = json.loads(data["sq-bridge.json"])
    assert "type: socks" in data["config.yaml"]
    assert "127.0.0.1:" in data["config.yaml"]
    assert bridge["inbounds"][0]["tag"] == "sq-in"
    assert bridge["inbounds"][0]["listen"] == "127.0.0.1"
    assert any("network_mode: host" in service for service in services)
    assert "sq://" in client
    if tor:
        assert any(rule.get("outboundTag") == "tor-udp-block" for rule in bridge["routing"]["rules"])
        assert "tor-sidecar/torrc" in data
    assert meta["protocol"] == "shadowquic"


def test_chain_shadowquic_only_allowed_as_entry():
    validate_chain([{"protocol": "shadowquic", "serverId": "a"}, {"protocol": "tor", "serverId": "b"}])
    with pytest.raises(ValueError, match="ShadowQUIC"):
        validate_chain([{"protocol": "xray", "serverId": "a"}, {"protocol": "shadowquic", "serverId": "b"}])


def test_shadowquic_runtime_health():
    from hoplyra.health_check import check_runtime

    remote = runner()
    remote.run.side_effect = [(0, "running 4242\n", ""), (0, "UNCONN 0 0 0.0.0.0:443 0.0.0.0:*\n", "")]
    state, _ = check_runtime({"id": "12345678", "protocol": "shadowquic", "meta_json": json.dumps({"listenPort": 443})}, remote)
    assert state == "alive"
    assert "'{{.State.Status}} {{.State.Pid}}'" in remote.run.call_args_list[0][0][0]
    assert "nsenter -t 4242 -n ss -H -lun 'sport = :443'" == remote.run.call_args_list[1][0][0]


@pytest.mark.parametrize("same_server", [False, True])
def test_chain_entry_compose_integration(same_server):
    remote = runner()
    plan = build_plan(
        [
            {"id": "entry", "protocol": "shadowquic", "serverId": "a"},
            {"id": "exit", "protocol": "tor", "serverId": "a" if same_server else "b"},
        ],
        {"a": "192.0.2.1", "b": "192.0.2.2"},
    )
    deployer = ChainDeployer("/tmp/hoplyra")
    with (
        patch("hoplyra.chains.deployer._ensure_compose_images"),
        patch("hoplyra.chains.deployer.podman_compose_up"),
        patch("hoplyra.chains.deployer.ensure_openvpn_image"),
    ):
        if same_server:
            client, path, meta = deployer._deploy_single_server_chain(remote, "12345678", plan, plan.hops)
        else:
            client, path, meta = deployer._deploy_entry_server(
                remote, "12345678", plan, [plan.entry], ExitEndpoint("192.0.2.2", 9050, via_tor=True)
            )
    uploaded = {call.args[0].split("/")[-1]: call.args[1] for call in remote.upload_text.call_args_list}
    assert "hoplyra-shadowquic:1" in uploaded["docker-compose.yml"]
    assert "sq-bridge" in uploaded["docker-compose.yml"]
    assert "sq://" in client
    assert meta["listenPort"] == 443


def test_shadowquic_peer_lifecycle(tmp_path):
    config, _, client, _ = shadowquic_bundle(str(tmp_path), "cv-sq-test", "192.0.2.1", 443)
    path = tmp_path / "config.yaml"
    path.write_text(config)
    cfg = {"id": "test", "instance_path": str(tmp_path), "client_config": client}
    remote = runner()

    def execute(command, **kwargs):
        script = shlex.split(command)[2]

        def mock_subp(cmd, *a, **kw):
            if cmd[0] in ("podman", "docker"):
                if len(cmd) > 1 and cmd[1] == "inspect":
                    assert cmd[cmd.index("-f") + 1] == "{{.State.Status}} {{.State.Pid}}"
                    return MagicMock(returncode=0, stdout="running 4242\n")
                return MagicMock(returncode=0, stdout="")
            if cmd[0] == "nsenter":
                assert cmd == ["nsenter", "-t", "4242", "-n", "ss", "-H", "-lun", "sport = :443"]
                return MagicMock(returncode=0, stdout="UNCONN 0 0 0.0.0.0:443 0.0.0.0:*\n")
            raise AssertionError(f"Unexpected subprocess command: {cmd}")

        with patch.object(subprocess, "run", side_effect=mock_subp):
            exec(compile(script, "<remote-sq-users>", "exec"), {})
        return 0, "", ""

    remote.run.side_effect = execute
    username, peer_client = _create_shadowquic_peer(cfg, remote, "Phone #1")
    pw_line = next(line for line in peer_client.splitlines() if line.strip().startswith("password:"))
    password = json.loads(pw_line.split("password:", 1)[1].strip())
    assert f"sq://{username}:" in peer_client
    assert "#Phone%20%231" in peer_client
    assert "sq://primary:" not in peer_client

    users_line = next(line for line in path.read_text().splitlines() if line.strip().startswith("users:"))
    users = json.loads(users_line.split("users:", 1)[1].strip())
    assert {u["username"]: u["password"] for u in users}[username] == password
    assert any(u["username"] == "primary" for u in users)

    _remove_shadowquic_peer(cfg, remote, username, password)
    users_line = next(line for line in path.read_text().splitlines() if line.strip().startswith("users:"))
    users = json.loads(users_line.split("users:", 1)[1].strip())
    assert [u["username"] for u in users] == ["primary"]
    assert "jls-upstream:" in path.read_text()


def test_shadowquic_auth_failure_is_sanitized():
    remote = runner()
    remote.run.return_value = (1, "", "secret-password")
    with pytest.raises(RuntimeError, match="ShadowQUIC") as error:
        _update_shadowquic_users({"id": "test"}, remote, "user", "secret-password")
    assert "secret-password" not in str(error.value)
    remote.reset_mock()
    assert get_peer_stats({"protocol": "shadowquic"}, remote) == {}
    remote.run.assert_not_called()


def test_shadowquic_restart_failure_restores_config(tmp_path):
    path = tmp_path / "config.yaml"
    original = '    users: [{"username": "primary", "password": "test"}]\n'
    path.write_text(original)
    remote = runner()

    def execute(command, **kwargs):
        with patch.object(subprocess, "run", return_value=MagicMock(returncode=1)):
            exec(shlex.split(command)[2], {})

    remote.run.side_effect = execute
    with pytest.raises(RuntimeError, match="restart failed"):
        _update_shadowquic_users({"id": "test", "instance_path": str(tmp_path)}, remote, "new", "test-only")
    assert path.read_text() == original


def test_shadowquic_restart_success_but_process_dies_rolls_back(tmp_path):
    path = tmp_path / "config.yaml"
    original = '    users: [{"username": "primary", "password": "test"}]\n'
    path.write_text(original)
    remote = runner()

    call_count = {"restarts": 0}

    def execute(command, **kwargs):
        script = shlex.split(command)[2]

        def mock_subp(cmd, *a, **kw):
            if cmd[0] in ("podman", "docker"):
                if len(cmd) > 1 and cmd[1] == "version":
                    return MagicMock(returncode=0, stdout="")
                if len(cmd) > 1 and cmd[1] == "restart":
                    call_count["restarts"] += 1
                    return MagicMock(returncode=0)
                if len(cmd) > 1 and cmd[1] == "inspect":
                    assert cmd[cmd.index("-f") + 1] == "{{.State.Status}} {{.State.Pid}}"
                    # First restart: process died after restart
                    if call_count["restarts"] == 1:
                        return MagicMock(returncode=0, stdout="exited 0\n")
                    # Second restart (rollback): process is alive
                    return MagicMock(returncode=0, stdout="running 4242\n")
            if cmd[0] == "nsenter":
                assert cmd == ["nsenter", "-t", "4242", "-n", "ss", "-H", "-lun", "sport = :443"]
                return MagicMock(returncode=0, stdout="UNCONN 0 0 0.0.0.0:443 0.0.0.0:*\n")
            raise AssertionError(f"Unexpected subprocess command: {cmd}")

        with patch.object(subprocess, "run", side_effect=mock_subp):
            exec(compile(script, "<remote-sq-users>", "exec"), {})

    remote.run.side_effect = execute
    with pytest.raises(RuntimeError, match="rolled back"):
        _update_shadowquic_users({"id": "test", "instance_path": str(tmp_path)}, remote, "new", "test-only")

    assert path.read_text() == original
    assert call_count["restarts"] == 2


def test_shadowquic_nsenter_missing_triggers_rollback(tmp_path):
    path = tmp_path / "config.yaml"
    original = '    users: [{"username": "primary", "password": "test"}]\n'
    path.write_text(original)
    remote = runner()
    restarts = []

    def execute(command, **kwargs):
        script = shlex.split(command)[2]

        def mock_subp(cmd, *a, **kw):
            if cmd[0] in ("podman", "docker"):
                if len(cmd) > 1 and cmd[1] == "version":
                    return MagicMock(returncode=0, stdout="")
                if len(cmd) > 1 and cmd[1] == "restart":
                    restarts.append(path.read_text())
                    return MagicMock(returncode=0)
                if len(cmd) > 1 and cmd[1] == "inspect":
                    assert cmd[cmd.index("-f") + 1] == "{{.State.Status}} {{.State.Pid}}"
                    return MagicMock(returncode=0, stdout="running 4242\n")
            if cmd[0] == "nsenter":
                raise FileNotFoundError("nsenter not found")
            raise AssertionError(f"Unexpected subprocess command: {cmd}")

        with patch.object(subprocess, "run", side_effect=mock_subp):
            exec(compile(script, "<remote-sq-users>", "exec"), {})

    remote.run.side_effect = execute
    with pytest.raises(RuntimeError) as exc:
        _update_shadowquic_users({"id": "test", "instance_path": str(tmp_path)}, remote, "new", "test-only")

    assert path.read_text() == original
    assert len(restarts) == 2
    assert restarts[0] != original  # updated config was active on first restart
    assert restarts[1] == original  # original config restored before second restart
    msg = str(exc.value)
    assert "rolled back" in msg
    assert "could not be verified" in msg
    assert "nsenter not found" in msg


def test_shadowquic_peer_binds_to_docker_runtime(tmp_path):
    path = tmp_path / "config.yaml"
    original = '    users: [{"username": "primary", "password": "test"}]\n'
    path.write_text(original)
    remote = runner()
    seen_cmds = []

    def execute(command, **kwargs):
        script = shlex.split(command)[2]

        def mock_subp(cmd, *a, **kw):
            seen_cmds.append(cmd)
            if cmd[0] == "docker" and cmd[1] == "restart":
                return MagicMock(returncode=0)
            if cmd[0] == "docker" and cmd[1] == "inspect":
                assert cmd[cmd.index("-f") + 1] == "{{.State.Status}} {{.State.Pid}}"
                return MagicMock(returncode=0, stdout="running 9001\n")
            if cmd[0] == "nsenter":
                assert cmd == ["nsenter", "-t", "9001", "-n", "ss", "-H", "-lun", "sport = :8443"]
                return MagicMock(returncode=0, stdout="UNCONN 0 0 0.0.0.0:8443 0.0.0.0:*\n")
            raise AssertionError(f"Unexpected command (should bind directly to docker): {cmd}")

        with patch.object(subprocess, "run", side_effect=mock_subp):
            exec(compile(script, "<remote-sq-users>", "exec"), {})
        return 0, "", ""

    remote.run.side_effect = execute
    _update_shadowquic_users(
        {
            "id": "test",
            "instance_path": str(tmp_path),
            "meta_json": json.dumps({"runtime": "docker compose", "listenPort": 8443}),
        },
        remote,
        "new-peer",
        "test-pw",
    )
    assert seen_cmds[0][:2] == ["docker", "restart"]
    assert seen_cmds[1][:2] == ["docker", "inspect"]
    assert seen_cmds[2][0] == "nsenter"


def test_shadowquic_health_check_ignores_tcp_and_peer_port_without_netid():
    from hoplyra.health_check import check_runtime

    remote = runner()
    remote.run.side_effect = [
        (0, "running 4242\n", ""),
        # Without Netid column, parts[4] is peer port 443 while local port is 53.
        (0, "UNCONN 0 0 0.0.0.0:53 192.0.2.99:443\nLISTEN 0 128 0.0.0.0:443 0.0.0.0:*\n", ""),
    ]
    state, reason = check_runtime(
        {
            "id": "12345678",
            "protocol": "shadowquic",
            "meta_json": json.dumps({"listenPort": 443, "runtime": "podman compose"}),
        },
        remote,
    )
    assert state == "down"
    assert "not listening" in reason
    assert "podman inspect -f '{{.State.Status}} {{.State.Pid}}'" in remote.run.call_args_list[0][0][0]
    assert remote.run.call_args_list[1][0][0] == "nsenter -t 4242 -n ss -H -lun 'sport = :443'"


def test_shadowquic_running_udp_published_on_host_but_not_in_container_netns():
    from hoplyra.health_check import check_runtime

    # Even if container is running and UDP/443 is published on host proxy,
    # container netns has no UDP/443 listener yet -> health check must report down.
    remote = runner()
    remote.run.side_effect = [
        (0, "running 4242\n", ""),
        (0, "", ""),
    ]
    state, reason = check_runtime(
        {
            "id": "12345678",
            "protocol": "shadowquic",
            "meta_json": json.dumps({"listenPort": 443, "runtime": "docker compose"}),
        },
        remote,
    )
    assert state == "down"
    assert "not listening" in reason
    assert "docker inspect -f '{{.State.Status}} {{.State.Pid}}'" in remote.run.call_args_list[0][0][0]
    assert remote.run.call_args_list[1][0][0] == "nsenter -t 4242 -n ss -H -lun 'sport = :443'"


def test_shadowquic_health_check_binds_to_docker_runtime():
    from hoplyra.health_check import check_runtime

    remote = runner()
    remote.run.side_effect = [
        (0, "running 7777\n", ""),
        (0, "UNCONN 0 0 0.0.0.0:443 0.0.0.0:*\n", ""),
    ]
    state, _ = check_runtime(
        {
            "id": "12345678",
            "protocol": "shadowquic",
            "meta_json": json.dumps({"listenPort": 443, "runtime": "docker compose"}),
        },
        remote,
    )
    assert state == "alive"
    assert "docker inspect -f '{{.State.Status}} {{.State.Pid}}'" in remote.run.call_args_list[0][0][0]
    assert remote.run.call_args_list[1][0][0] == "nsenter -t 7777 -n ss -H -lun 'sport = :443'"


def test_deploy_readiness_failure_cleans_up():
    remote = runner()
    remote.run.side_effect = [
        (2, "", "container exited with code 1"),
        (0, "FATAL: address already in use\n", ""),
    ]
    with (
        patch("hoplyra.protocols.mkdir_remote"),
        patch("hoplyra.protocols.ensure_image"),
        patch("hoplyra.protocols.podman_compose_up", return_value="docker compose"),
        patch("hoplyra.protocols.podman_compose_down") as down,
    ):
        with pytest.raises(RuntimeError, match="address already in use"):
            ShadowQuicDeployer().deploy(remote, "12345678-abcd", "192.0.2.1", resources={"listenPort": 444})
    check_cmd = remote.run.call_args_list[0][0][0]
    assert "{{.State.Status}} {{.State.Pid}}" in check_cmd
    assert "nsenter -t" in check_cmd
    assert "sport = :444" in check_cmd
    down.assert_called_once_with(
        remote,
        "/opt/hoplyra/instances/12345678-abcd",
        "cv-shadowquic-12345678",
        strict=True,
        runtime="docker compose",
    )


def test_deploy_readiness_and_cleanup_failure_reports_both():
    remote = runner()
    remote.run.side_effect = [
        (1, "", ""),
        (0, "startup hung before bind\n", ""),
    ]
    with (
        patch("hoplyra.protocols.mkdir_remote"),
        patch("hoplyra.protocols.ensure_image"),
        patch("hoplyra.protocols.podman_compose_up", return_value="docker compose"),
        patch("hoplyra.protocols.podman_compose_down", side_effect=RuntimeError("compose down failed (docker compose): daemon error")) as down,
    ):
        with pytest.raises(RuntimeError) as exc:
            ShadowQuicDeployer().deploy(remote, "12345678-abcd", "192.0.2.1", resources={"listenPort": 444})
    msg = str(exc.value)
    assert "startup hung before bind" in msg
    assert "cleanup failed: compose down failed (docker compose): daemon error" in msg
    down.assert_called_once_with(
        remote,
        "/opt/hoplyra/instances/12345678-abcd",
        "cv-shadowquic-12345678",
        strict=True,
        runtime="docker compose",
    )
