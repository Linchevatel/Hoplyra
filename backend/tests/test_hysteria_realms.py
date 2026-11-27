from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import uuid

import pytest
import yaml
from fastapi.testclient import TestClient

from hoplyra.db import connect, init_db, new_id
from hoplyra.main import app
from hoplyra.realms import (
    REALM_TUNNEL_USERNAME,
    RealmError,
    _normalize_uuid,
    _validate_safe_realm_path,
    build_tunnel_client_material,
    deploy_realm_broker,
    deploy_realm_tunnel,
    get_active_broker,
    get_broker_by_id,
    get_broker_logs,
    get_tunnel_by_id,
    get_tunnel_logs,
    list_brokers,
    list_tunnels,
    stop_realm_broker,
    start_realm_broker,
    delete_realm_broker,
    stop_realm_tunnel,
    start_realm_tunnel,
    delete_realm_tunnel,
    wait_broker_ready,
    wait_tunnel_ready,
)


def mock_runner(host="198.51.100.1"):
    mock = MagicMock()
    mock.target = SimpleNamespace(is_local=False, host=host)

    def _default_run(cmd, *args, **kwargs):
        if "command -v podman" in cmd or "podman --version" in cmd:
            return (0, "ok\n", "")
        if "podman compose version" in cmd or "command -v podman-compose" in cmd:
            return (0, "ok\n", "")
        if "docker" in cmd:
            return (1, "", "command not found: docker\n")
        if "State.Running" in cmd:
            return (0, "false\n", "")
        if "State.Status" in cmd or "inspect" in cmd:
            return (0, 'running "2026-11-27T15:00:00Z"\n', "")
        if "logs" in cmd:
            return (0, "2026-11-27T15:00:01Z realm registered\n2026-11-27T15:00:01Z hysteria realm server listening on :8443\n", "")
        if "sport =" in cmd:
            return (0, "LISTEN 0 128 *:8443\n", "")
        return (0, "", "")

    mock.run.side_effect = _default_run
    return mock


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_realms.db"
    monkeypatch.setattr("hoplyra.db.DATA_DIR", tmp_path)
    monkeypatch.setattr("hoplyra.db.DB_PATH", test_db)
    init_db()


def create_test_server(name="VPS-1", host="198.51.100.1") -> dict:
    server_id = new_id()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO servers (id, name, host, port, username, auth_type, auth_secret, status, tags, created_at)
            VALUES (?, ?, ?, 22, 'root', 'password', 'mypass', 'online', '[]', datetime('now'))
            """,
            (server_id, name, host),
        )
    return {"id": server_id, "name": name, "host": host}


def test_build_tunnel_client_material():
    materials = build_tunnel_client_material(
        broker_url="realm://public@realm.hy2.io",
        realm_name="my-test-room",
        auth_password="secret-password-123",
        pin_sha256="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        tunnel_name="HomePC",
    )
    assert materials["realmEndpoint"] == "realm://public@realm.hy2.io/my-test-room"
    
    # Parse YAML to verify structure and credential alignment
    cfg = yaml.safe_load(materials["yaml"])
    assert cfg["server"] == "realm://public@realm.hy2.io/my-test-room"
    assert cfg["auth"] == "primary:secret-password-123"
    assert cfg["tls"]["pinSHA256"] == "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"

    assert "hysteria2://" in materials["shareUri"]
    assert "realm=my-test-room" in materials["shareUri"]
    assert "token=public" in materials["shareUri"]
    assert "primary%3Asecret-password-123" in materials["shareUri"]


def test_deploy_realm_broker_lifecycle():
    server = create_test_server(name="MyBrokerVPS", host="198.51.100.5")
    remote = mock_runner(host="198.51.100.5")

    with patch("hoplyra.realms.ensure_image") as mock_img, patch("hoplyra.realms.podman_compose_up") as mock_up:
        broker = deploy_realm_broker(
            runner=remote,
            server=server,
            listen_port=8443,
            token="my-custom-token-xyz",
            domain_or_ip="broker.example.com",
        )

    assert broker["serverId"] == server["id"]
    assert broker["listenPort"] == 8443
    assert broker["token"] == "my-custom-token-xyz"
    assert broker["domainOrIp"] == "broker.example.com"
    assert broker["status"] == "active"
    assert broker["instancePath"] == f"/opt/hoplyra/realm-broker-{broker['id']}"
    assert broker["containerName"] == f"cv-realm-broker-{broker['id']}"
    mock_img.assert_called_once_with(remote, "hoplyra-realm-server:1", "hysteria-realm-server")
    mock_up.assert_called_once()

    # Verify uploaded compose file
    files = {call.args[0].split("/")[-1]: call.args[1] for call in remote.upload_text.call_args_list}
    assert "docker-compose.yml" in files
    assert "HYSTERIA_REALM_TOKEN: \"my-custom-token-xyz\"" in files["docker-compose.yml"]
    assert "HYSTERIA_REALM_LISTEN: \":8443\"" in files["docker-compose.yml"]
    assert "hoplyra-realm-server:1" in files["docker-compose.yml"]

    # Active broker check
    active = get_active_broker()
    assert active is not None
    assert active["id"] == broker["id"]

    # Stop broker
    with patch("hoplyra.realms.podman_compose_down") as mock_down:
        stop_realm_broker(broker["id"], remote)
        mock_down.assert_called_once()
        b_stopped = get_broker_by_id(broker["id"])
        assert b_stopped["status"] == "inactive"

    # Start broker
    with patch("hoplyra.realms.podman_compose_up") as mock_start:
        start_realm_broker(broker["id"], remote)
        mock_start.assert_called_once()
        b_active = get_broker_by_id(broker["id"])
        assert b_active["status"] == "active"

    # Delete broker
    with patch("hoplyra.realms.podman_compose_down"):
        delete_realm_broker(broker["id"], remote)
        assert get_broker_by_id(broker["id"]) is None


def test_deploy_realm_tunnel_public_and_self_hosted():
    home_server = create_test_server(name="HomeServer", host="10.0.0.5")
    remote = mock_runner(host="10.0.0.5")

    # 1. Deploy tunnel with PUBLIC broker
    with patch("hoplyra.realms.ensure_image") as mock_img, patch("hoplyra.realms.podman_compose_up") as mock_up:
        tunnel_pub = deploy_realm_tunnel(
            runner=remote,
            server=home_server,
            name="Home-Public",
            broker_type="public",
            realm_name="room-pub-123",
            auth_password="mypassword1",
        )

    assert tunnel_pub["status"] == "active"
    assert tunnel_pub["brokerType"] == "public"
    assert tunnel_pub["brokerUrl"] == "realm://public@realm.hy2.io"
    assert tunnel_pub["realmName"] == "room-pub-123"
    assert tunnel_pub["instancePath"] == f"/opt/hoplyra/realm-tunnel-{tunnel_pub['id']}"
    assert tunnel_pub["containerName"] == f"cv-realm-tun-{tunnel_pub['id']}"

    files = {call.args[0].split("/")[-1]: call.args[1] for call in remote.upload_text.call_args_list}
    assert "config.yaml" in files
    assert "listen: \"realm://public@realm.hy2.io/room-pub-123\"" in files["config.yaml"]
    assert "server.crt" in files
    assert "server.key" in files
    assert "docker-compose.yml" in files
    assert "hoplyra-hysteria2:1" in files["docker-compose.yml"]

    # 2. Deploy tunnel with SELF-HOSTED broker
    broker_server = create_test_server(name="VPS-Broker", host="203.0.113.10")
    b_remote = mock_runner(host="203.0.113.10")
    with patch("hoplyra.realms.ensure_image"), patch("hoplyra.realms.podman_compose_up"):
        deploy_realm_broker(b_remote, broker_server, listen_port=9443, token="tok-999")

    remote.upload_text.reset_mock()
    with patch("hoplyra.realms.ensure_image"), patch("hoplyra.realms.podman_compose_up"):
        tunnel_priv = deploy_realm_tunnel(
            runner=remote,
            server=home_server,
            name="Home-Private",
            broker_type="self_hosted",
            realm_name="room-priv-456",
        )

    assert tunnel_priv["brokerType"] == "self_hosted"
    assert "realm://tok-999@203.0.113.10:9443" in tunnel_priv["brokerUrl"]
    files_priv = {call.args[0].split("/")[-1]: call.args[1] for call in remote.upload_text.call_args_list}
    assert "listen: \"realm://tok-999@203.0.113.10:9443/room-priv-456\"" in files_priv["config.yaml"]

    # Stop and delete tunnel
    with patch("hoplyra.realms.podman_compose_down") as mock_down:
        stop_realm_tunnel(tunnel_priv["id"], remote)
        mock_down.assert_called_once()
        assert get_tunnel_by_id(tunnel_priv["id"])["status"] == "inactive"

    with patch("hoplyra.realms.podman_compose_down"):
        delete_realm_tunnel(tunnel_priv["id"], remote)
        assert get_tunnel_by_id(tunnel_priv["id"]) is None


def test_api_realms_flow():
    client = TestClient(app)
    login_resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login_resp.status_code == 200

    server = create_test_server(name="APIServer", host="192.0.2.77")

    # Status before anything
    resp = client.get("/api/realms/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["activeBroker"] is None
    assert data["tunnels"] == []

    # Deploy broker via API
    with patch("hoplyra.main.RemoteRunner") as mock_rr, patch("hoplyra.realms.ensure_image"), patch("hoplyra.realms.podman_compose_up"):
        mock_rr.return_value = mock_runner(host="192.0.2.77")
        resp_b = client.post(
            "/api/realms/brokers",
            json={"serverId": server["id"], "listenPort": 8443, "token": "api-token-123"},
        )
        assert resp_b.status_code == 200
        b_data = resp_b.json()
        assert b_data["token"] == "api-token-123"
        broker_id = b_data["id"]

    # Deploy tunnel via API
    with patch("hoplyra.main.RemoteRunner") as mock_rr, patch("hoplyra.realms.ensure_image"), patch("hoplyra.realms.podman_compose_up"):
        mock_rr.return_value = mock_runner(host="192.0.2.77")
        resp_t = client.post(
            "/api/realms/tunnels",
            json={
                "serverId": server["id"],
                "name": "API-Tunnel",
                "brokerType": "public",
                "realmName": "api-room-99",
            },
        )
        assert resp_t.status_code == 200
        t_data = resp_t.json()
        assert t_data["name"] == "API-Tunnel"
        tunnel_id = t_data["id"]

    # Get tunnel config
    resp_cfg = client.get(f"/api/realms/tunnels/{tunnel_id}/config")
    assert resp_cfg.status_code == 200
    cfg_data = resp_cfg.json()
    assert "realm://public@realm.hy2.io/api-room-99" in cfg_data["realmEndpoint"]
    assert "hysteria2://" in cfg_data["shareUri"]

    # Test logs endpoints: RemoteRunner.run returns (code, stdout, stderr)
    with patch("hoplyra.main.RemoteRunner") as mock_rr:
        mock_runner_instance = mock_runner(host="192.0.2.77")
        mock_runner_instance.run.return_value = (0, "2026-11-27 INFO server listening on :8443", "")
        mock_rr.return_value = mock_runner_instance
        resp_b_logs = client.get(f"/api/realms/brokers/{broker_id}/logs")
        assert resp_b_logs.status_code == 200
        assert "server listening on :8443" in resp_b_logs.json()["logs"]

        resp_t_logs = client.get(f"/api/realms/tunnels/{tunnel_id}/logs")
        assert resp_t_logs.status_code == 200
        assert "server listening on :8443" in resp_t_logs.json()["logs"]

    # Delete tunnel and broker via API
    with patch("hoplyra.main.RemoteRunner") as mock_rr, patch("hoplyra.realms.podman_compose_down"):
        mock_rr.return_value = mock_runner(host="192.0.2.77")
        resp_dt = client.delete(f"/api/realms/tunnels/{tunnel_id}")
        assert resp_dt.status_code == 200

        resp_db = client.delete(f"/api/realms/brokers/{broker_id}")
        assert resp_db.status_code == 200


def test_async_realms_deploy():
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
    server = create_test_server(name="AsyncServer", host="192.0.2.88")

    with patch("hoplyra.main.RemoteRunner") as mock_rr, patch("hoplyra.realms.ensure_image"), patch("hoplyra.realms.podman_compose_up"):
        mock_rr.return_value = mock_runner(host="192.0.2.88")
        # Async broker deploy returns status 'deploying' immediately
        resp_b = client.post(
            "/api/realms/brokers",
            json={"serverId": server["id"], "listenPort": 8443, "token": "tok-async", "asyncDeploy": True},
        )
        assert resp_b.status_code == 200
        b_data = resp_b.json()
        assert b_data["status"] == "deploying"
        assert b_data["token"] == "tok-async"

        # Async tunnel deploy returns status 'deploying' immediately
        resp_t = client.post(
            "/api/realms/tunnels",
            json={
                "serverId": server["id"],
                "name": "Async-Tunnel",
                "brokerType": "public",
                "realmName": "async-room-1",
                "asyncDeploy": True,
            },
        )
        assert resp_t.status_code == 200
        t_data = resp_t.json()
        assert t_data["status"] == "deploying"
        assert t_data["name"] == "Async-Tunnel"

        import threading
        for th in list(threading.enumerate()):
            if th.name.startswith("hoplyra-broker-") or th.name.startswith("hoplyra-tunnel-"):
                th.join(timeout=2.0)



# ---------------------------------------------------------------------------
# Specific Tests for the 5 Code Review Items
# ---------------------------------------------------------------------------

def test_uuid_validation_and_no_collision():
    """Item 1: UUID validation and prevention of resource collision between same-prefix IDs."""
    server = create_test_server(name="CollisionVPS", host="192.0.2.50")
    remote = mock_runner(host="192.0.2.50")

    # Two different UUIDs sharing the exact same 8-character prefix
    id1 = "a1b2c3d4-0000-0000-0000-000000000001"
    id2 = "a1b2c3d4-0000-0000-0000-000000000002"

    with patch("hoplyra.realms.ensure_image"), patch("hoplyra.realms.podman_compose_up"):
        t1 = deploy_realm_tunnel(remote, server, name="T1", tunnel_id=id1)
        t2 = deploy_realm_tunnel(remote, server, name="T2", tunnel_id=id2)

    assert t1["id"] == id1
    assert t2["id"] == id2
    # Verify their instance paths and container names DO NOT COLLIDE
    assert t1["instancePath"] != t2["instancePath"]
    assert t1["containerName"] != t2["containerName"]
    assert t1["instancePath"] == f"/opt/hoplyra/realm-tunnel-{id1}"
    assert t2["instancePath"] == f"/opt/hoplyra/realm-tunnel-{id2}"

    # Verify invalid UUID rejected by _normalize_uuid
    with pytest.raises(RealmError, match="Invalid UUID"):
        _normalize_uuid("invalid-not-a-uuid")
    with pytest.raises(RealmError, match="Invalid UUID"):
        _normalize_uuid("../../etc/passwd")

    # Verify path validation rejects unsafe paths
    with pytest.raises(RealmError, match="Instance path must be absolute"):
        _validate_safe_realm_path("relative/path", "realm-tunnel-")
    with pytest.raises(RealmError, match="Instance path parent must be /opt/hoplyra"):
        _validate_safe_realm_path("/etc/passwd", "realm-tunnel-")
    with pytest.raises(RealmError, match="Instance directory name must start with"):
        _validate_safe_realm_path("/opt/hoplyra/wrong-prefix-123", "realm-tunnel-")


def test_auth_yaml_matches_server_userpass():
    """Item 2: Client YAML and share URI credentials match server userpass."""
    server = create_test_server(name="AuthTestVPS", host="192.0.2.60")
    remote = mock_runner(host="192.0.2.60")

    auth_pass = "my-secret-realm-pass"
    with patch("hoplyra.realms.ensure_image"), patch("hoplyra.realms.podman_compose_up"):
        tunnel = deploy_realm_tunnel(
            remote,
            server,
            name="AuthRoom",
            auth_password=auth_pass,
        )

    # Inspect uploaded server config.yaml
    files = {call.args[0].split("/")[-1]: call.args[1] for call in remote.upload_text.call_args_list}
    server_yaml = yaml.safe_load(files["config.yaml"])
    
    assert server_yaml["auth"]["type"] == "userpass"
    assert REALM_TUNNEL_USERNAME in server_yaml["auth"]["userpass"]
    assert server_yaml["auth"]["userpass"][REALM_TUNNEL_USERNAME] == auth_pass

    # Inspect client config.yaml
    client_yaml = yaml.safe_load(tunnel["clientConfig"])
    expected_client_auth = f"{REALM_TUNNEL_USERNAME}:{auth_pass}"
    assert client_yaml["auth"] == expected_client_auth

    # Verify client auth splits into username:password matching server config
    u, p = client_yaml["auth"].split(":", 1)
    assert u == REALM_TUNNEL_USERNAME
    assert p == auth_pass
    assert server_yaml["auth"]["userpass"][u] == p


def test_delete_failure_preserves_error_status():
    """Item 1 regression: Real runner.run() failure during compose down preserves DB record and files."""
    server = create_test_server(name="FailDeleteVPS", host="192.0.2.70")
    remote = mock_runner(host="192.0.2.70")

    with patch("hoplyra.realms.ensure_image"), patch("hoplyra.realms.podman_compose_up"):
        broker = deploy_realm_broker(remote, server, listen_port=8443)

    commands_executed: list[str] = []

    def mock_run_side_effect(cmd, *args, **kwargs):
        commands_executed.append(cmd)
        if "docker info" in cmd or "podman compose version" in cmd or "docker compose version" in cmd:
            return (0, "docker compose ok", "")
        if "test -d" in cmd:
            return (0, "", "")
        if "compose" in cmd and "down" in cmd:
            # Simulate real compose down failing at runner level
            return (1, "", "Error: container busy or network unreachable")
        if "rm -rf" in cmd:
            return (0, "", "")
        return (0, "running 2026-11-27T15:00:00Z\n", "")

    remote.run.side_effect = mock_run_side_effect

    with pytest.raises(RealmError, match="Failed to delete realm broker"):
        delete_realm_broker(broker["id"], remote)

    # Record must still exist in SQLite with status='error'
    stored = get_broker_by_id(broker["id"])
    assert stored is not None
    assert stored["status"] == "error"

    # CRITICAL: rm -rf MUST NOT have been executed because compose down failed!
    assert not any("rm -rf" in c for c in commands_executed)

    # Now simulate retry succeeding
    commands_executed.clear()

    def mock_retry_run(cmd, *args, **kwargs):
        commands_executed.append(cmd)
        if "docker info" in cmd or "docker compose version" in cmd:
            return (0, "docker compose ok", "")
        if "test -d" in cmd:
            return (0, "", "")
        if "compose" in cmd and "down" in cmd:
            return (0, "", "")
        if "State.Running" in cmd:
            return (0, "false\n", "")
        if "rm -rf" in cmd:
            return (0, "", "")
        return (0, "", "")

    remote.run.side_effect = mock_retry_run
    delete_realm_broker(broker["id"], remote)
    assert get_broker_by_id(broker["id"]) is None
    assert any("rm -rf" in c for c in commands_executed)


def test_delete_failure_when_container_still_running():
    """Item 1: If container is still running after compose down, abort cleanup and keep record."""
    server = create_test_server(name="RunningVPS", host="192.0.2.71")
    remote = mock_runner(host="192.0.2.71")

    with patch("hoplyra.realms.ensure_image"), patch("hoplyra.realms.podman_compose_up"):
        tunnel = deploy_realm_tunnel(remote, server, name="RunningRoom")

    commands_executed: list[str] = []

    def mock_run(cmd, *args, **kwargs):
        commands_executed.append(cmd)
        if "compose" in cmd and "down" in cmd:
            return (0, "", "")
        if "State.Running" in cmd:
            # Container still reports running!
            return (0, "true\n", "")
        if "rm -rf" in cmd:
            return (0, "", "")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with pytest.raises(RealmError, match="is still running"):
        delete_realm_tunnel(tunnel["id"], remote)

    stored = get_tunnel_by_id(tunnel["id"])
    assert stored is not None
    assert stored["status"] == "error"
    # rm -rf must not run
    assert not any("rm -rf" in c for c in commands_executed)


def test_start_verifies_readiness_and_marks_error_on_failure():
    """Item 2: 'Start' executes readiness checks and sets status to 'error' on timeout."""
    server = create_test_server(name="StartVPS", host="192.0.2.72")
    remote = mock_runner(host="192.0.2.72")

    with patch("hoplyra.realms.ensure_image"), patch("hoplyra.realms.podman_compose_up"):
        broker = deploy_realm_broker(remote, server, listen_port=8443)
        tunnel = deploy_realm_tunnel(remote, server, name="StartRoom")

    # Stop them cleanly
    with patch("hoplyra.realms.podman_compose_down"):
        stop_realm_broker(broker["id"], remote)
        stop_realm_tunnel(tunnel["id"], remote)

    assert get_broker_by_id(broker["id"])["status"] == "inactive"
    assert get_tunnel_by_id(tunnel["id"])["status"] == "inactive"

    # Start broker but readiness times out / fails
    with patch("hoplyra.realms.podman_compose_up"), patch(
        "hoplyra.realms.wait_broker_ready", side_effect=RealmError("Port not reachable")
    ):
        with pytest.raises(RealmError, match="Broker failed to become ready on start"):
            start_realm_broker(broker["id"], remote)
        assert get_broker_by_id(broker["id"])["status"] == "error"

    # Start tunnel but registration times out / fails
    with patch("hoplyra.realms.podman_compose_up"), patch(
        "hoplyra.realms.wait_tunnel_ready", side_effect=RealmError("Room registration timed out")
    ):
        with pytest.raises(RealmError, match="Tunnel failed to become ready on start"):
            start_realm_tunnel(tunnel["id"], remote)
        assert get_tunnel_by_id(tunnel["id"])["status"] == "error"

    # Successful start restores active status
    with patch("hoplyra.realms.podman_compose_up"), patch("hoplyra.realms.wait_broker_ready"):
        start_realm_broker(broker["id"], remote)
        assert get_broker_by_id(broker["id"])["status"] == "active"

    with patch("hoplyra.realms.podman_compose_up"), patch("hoplyra.realms.wait_tunnel_ready"):
        start_realm_tunnel(tunnel["id"], remote)
        assert get_tunnel_by_id(tunnel["id"])["status"] == "active"


def test_readiness_negative_port_busy_inspect_failed():
    """Item 3: Host port is occupied by a foreign process, but container inspect fails."""
    remote = MagicMock()

    def mock_run(cmd, *args, **kwargs):
        if "inspect" in cmd:
            # Container inspect fails (e.g. container does not exist)
            return (1, "", "Error: no such container")
        if "sport =" in cmd:
            # Foreign process is listening on the port
            return (0, "LISTEN 0 128 *:8443", "")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with pytest.raises(RealmError, match="Broker failed to become ready"):
        wait_broker_ready(remote, "cv-realm-broker-test", 8443, timeout=1)


def test_readiness_negative_old_log_only():
    """Item 3: 'realm registered' is only present in logs prior to container StartedAt."""
    remote = MagicMock()
    container_started = "2026-11-27T15:25:00Z"

    def mock_run_old(cmd, *args, **kwargs):
        if "inspect" in cmd:
            return (0, f"running {container_started}\n", "")
        if "logs" in cmd:
            # Old log from 5 minutes before container start
            return (0, '2026-11-27T15:20:00Z realm registered {"realm": "my-room"}\n', "")
        return (0, "", "")

    remote.run.side_effect = mock_run_old

    # Must ignore old log and time out with RealmError
    with pytest.raises(RealmError, match="Tunnel failed to register room"):
        wait_tunnel_ready(remote, "cv-realm-tun-test", "my-room", timeout=1)

    # Now simulate new log emitted at 15:25:05Z (after StartedAt)
    def mock_run_new(cmd, *args, **kwargs):
        if "inspect" in cmd:
            return (0, f"running {container_started}\n", "")
        if "logs" in cmd:
            return (0, '2026-11-27T15:25:05Z realm registered {"realm": "my-room"}\n', "")
        return (0, "", "")

    remote.run.side_effect = mock_run_new
    wait_tunnel_ready(remote, "cv-realm-tun-test", "my-room", timeout=2)


def test_delete_failure_when_inspect_returns_daemon_error():
    """Item 1: Daemon error during inspect must NOT proceed with rm -rf and must preserve record."""
    server = create_test_server(name="DaemonErrVPS", host="192.0.2.73")
    remote = mock_runner(host="192.0.2.73")

    with patch("hoplyra.realms.ensure_image"), patch("hoplyra.realms.podman_compose_up"):
        broker = deploy_realm_broker(remote, server, listen_port=8443)

    commands_executed: list[str] = []

    def mock_run(cmd, *args, **kwargs):
        commands_executed.append(cmd)
        if "compose" in cmd and "down" in cmd:
            return (0, "", "")
        if "State.Running" in cmd:
            # Daemon is down / socket unreachable
            return (1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?")
        if "rm -rf" in cmd:
            return (0, "", "")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with pytest.raises(RealmError, match="runtime daemon error"):
        delete_realm_broker(broker["id"], remote)

    stored = get_broker_by_id(broker["id"])
    assert stored is not None
    assert stored["status"] == "error"
    # rm -rf MUST NOT be called!
    assert not any("rm -rf" in c for c in commands_executed)


def test_readiness_negative_running_without_ready_broker():
    """Item 3: Container running and host port open (runtime proxy), but broker process not yet listening."""
    remote = MagicMock()
    started_at = "2026-11-27T15:00:00Z"

    def mock_run_not_ready(cmd, *args, **kwargs):
        if "inspect" in cmd:
            return (0, f"running {started_at}\n", "")
        if "sport =" in cmd:
            # Port is listening on host via docker-proxy/pasta
            return (0, "LISTEN 0 128 *:8443\n", "")
        if "logs" in cmd:
            # Process is still generating certs, hasn't logged listening yet
            return (0, "2026-11-27T15:00:01Z generating self-signed certificates\n", "")
        return (0, "", "")

    remote.run.side_effect = mock_run_not_ready

    # Must NOT succeed based on host socket alone; must time out
    with pytest.raises(RealmError, match="Broker failed to become ready"):
        wait_broker_ready(remote, "cv-realm-broker-test", 8443, timeout=1)

    # When the process itself outputs the positive listening signal
    def mock_run_ready(cmd, *args, **kwargs):
        if "inspect" in cmd:
            return (0, f"running {started_at}\n", "")
        if "sport =" in cmd:
            return (0, "LISTEN 0 128 *:8443\n", "")
        if "logs" in cmd:
            return (0, "2026-11-27T15:00:02Z server listening on :8443\n", "")
        return (0, "", "")

    remote.run.side_effect = mock_run_ready
    wait_broker_ready(remote, "cv-realm-broker-test", 8443, timeout=2)


def test_delete_failure_docker_daemon_down_without_podman():
    """Item 1: Podman is not installed on host and Docker daemon is unreachable. Must NOT run rm -rf and must preserve record."""
    server = create_test_server(name="DockerOnlyVPS", host="192.0.2.74")
    remote = mock_runner(host="192.0.2.74")

    with patch("hoplyra.realms.ensure_image"), patch("hoplyra.realms.podman_compose_up"):
        broker = deploy_realm_broker(remote, server, listen_port=8443)
    with connect() as conn:
        conn.execute("UPDATE realms_brokers SET runtime = NULL, meta_json = '{}' WHERE id = ?", (broker["id"],))




    commands_executed: list[str] = []

    def mock_run_daemon_down(cmd, *args, **kwargs):
        commands_executed.append(cmd)
        if "test -d" in cmd:
            return (1, "", "")
        # Podman is not installed
        if "command -v podman" in cmd or "podman" in cmd:
            return (127, "", "bash: podman: command not found")
        # Docker daemon is down / socket unreachable
        if "docker info" in cmd or "inspect" in cmd or "compose" in cmd:
            return (1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?")
        if "rm -rf" in cmd:
            return (0, "", "")
        return (0, "", "")

    remote.run.side_effect = mock_run_daemon_down

    with pytest.raises(RealmError, match="runtime daemon error"):
        delete_realm_broker(broker["id"], remote)

    stored = get_broker_by_id(broker["id"])
    assert stored is not None
    assert stored["status"] == "error"
    # rm -rf MUST NOT be called!
    assert not any("rm -rf" in c for c in commands_executed)


def test_readiness_negative_missing_or_invalid_started_at():
    """Item 2: Inspect returns running but missing/invalid StartedAt. Must time out and raise RealmError."""
    remote = MagicMock()

    def mock_run(cmd, *args, **kwargs):
        if "inspect" in cmd:
            # Missing StartedAt (only status)
            return (0, "running\n", "")
        if "logs" in cmd:
            return (0, "2026-11-27T15:00:01Z hysteria realm server listening on :8443\n", "")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with pytest.raises(RealmError, match="Broker failed to become ready"):
        wait_broker_ready(remote, "cv-realm-broker-test", 8443, timeout=1)


def test_readiness_negative_not_listening_message():
    """Item 2: Log contains 'not listening' substring. Must NOT be accepted as ready."""
    remote = MagicMock()
    started_at = "2026-11-27T15:00:00Z"

    def mock_run(cmd, *args, **kwargs):
        if "inspect" in cmd:
            return (0, f"running {started_at}\n", "")
        if "logs" in cmd:
            return (0, "2026-11-27T15:00:05Z error: server not listening due to port conflict\n", "")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with pytest.raises(RealmError, match="Broker failed to become ready"):
        wait_broker_ready(remote, "cv-realm-broker-test", 8443, timeout=1)


def test_readiness_negative_unparsed_log_timestamp():
    """Item 2: Log line has unknown timestamp format. Must NOT confirm readiness."""
    remote = MagicMock()
    started_at = "2026-11-27T15:00:00Z"

    def mock_run(cmd, *args, **kwargs):
        if "inspect" in cmd:
            return (0, f"running {started_at}\n", "")
        if "logs" in cmd:
            # Corrupted/unknown timestamp
            return (0, "[CUSTOM-LOG] hysteria realm server listening on :8443\n", "")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with pytest.raises(RealmError, match="Broker failed to become ready"):
        wait_broker_ready(remote, "cv-realm-broker-test", 8443, timeout=1)


def test_readiness_signals():
    """Item 4: Normal readiness probes for broker and tunnel."""
    remote = MagicMock()

    # 1. Broker readiness success when container running and process confirms listening
    remote.run.return_value = (0, "running 2026-11-27T15:00:00Z\n2026-11-27T15:00:01Z server listening on :8443\n", "")
    wait_broker_ready(remote, "cv-realm-broker-test", 8443, timeout=2)

    # 2. Broker fails when container exited
    remote.run.return_value = (0, "exited", "")
    with pytest.raises(RealmError, match="Broker container exited unexpectedly"):
        wait_broker_ready(remote, "cv-realm-broker-test", 8443, timeout=2)

    # 3. Tunnel readiness success when "realm registered" is logged after start
    remote.run.return_value = (0, "running 2026-11-27T15:00:00Z\n2026-11-27T15:01:00Z realm registered\n", "")
    wait_tunnel_ready(remote, "cv-realm-tun-test", "my-room", timeout=2)

    # 4. Tunnel fails when container exited
    remote.run.return_value = (0, "exited", "")
    with pytest.raises(RealmError, match="Tunnel container exited unexpectedly"):
        wait_tunnel_ready(remote, "cv-realm-tun-test", "my-room", timeout=2)


def test_get_logs_unpacks_runner_tuple():
    """Item 5: Container log APIs correctly unpack (code, stdout, stderr)."""
    server = create_test_server(name="LogsVPS", host="192.0.2.90")
    remote = mock_runner(host="192.0.2.90")

    with patch("hoplyra.realms.ensure_image"), patch("hoplyra.realms.podman_compose_up"):
        tunnel = deploy_realm_tunnel(remote, server, name="LogsTunnel")
        broker = deploy_realm_broker(remote, server, listen_port=8443)

    remote.run.side_effect = None
    remote.run.return_value = (0, "2026-11-27 INFO server ready and listening", "")
    t_logs = get_tunnel_logs(tunnel["id"], remote, tail=50)
    assert "server ready and listening" in t_logs

    b_logs = get_broker_logs(broker["id"], remote, tail=50)
    assert "server ready and listening" in b_logs


def test_docker_without_compose_podman_deploy_delete_preserves_record():
    """Regression test: Docker is available without Compose, deploy chooses Podman Compose,
    stack directory is missing, but Podman container is still running.
    Delete must inspect Podman (not Docker), detect running container, and preserve SQLite record.
    """
    server = create_test_server(name="DockerNoComposeVPS", host="192.0.2.110")
    remote = MagicMock()
    remote.target = SimpleNamespace(is_local=False, host="192.0.2.110")

    commands_executed: list[str] = []

    def mock_run(cmd, *args, **kwargs):
        commands_executed.append(cmd)
        # Compound check in container_runtime_ready
        if "command -v podman" in cmd and "command -v docker" in cmd:
            return (0, "ok\n", "")
        # Combined docker info && docker compose version in _detect_compose_runtime
        if "docker info" in cmd and "docker compose" in cmd:
            # docker compose fails, so echo ok is never reached
            return (0, "", "")
        # Standalone docker info succeeds
        if "docker info" in cmd:
            return (0, "ok\n", "")
        # Docker compose is NOT installed
        if "docker compose" in cmd:
            return (1, "", "docker: 'compose' is not a docker command.\nSee 'docker --help'")
        # Podman compose is available
        if "podman compose" in cmd or "command -v podman" in cmd or "podman" in cmd and "version" in cmd:
            return (0, "podman-compose version 1.0.6\n", "")
        # Podman compose up
        if "podman compose" in cmd and "up" in cmd:
            return (0, "", "")
        # Podman inspect for readiness
        if "podman inspect" in cmd and "StartedAt" in cmd:
            return (0, 'running "2026-11-27T15:00:00Z"\n', "")
        # Podman logs for readiness
        if "podman logs" in cmd:
            return (0, "2026-11-27T15:00:02Z server listening on :8443\n", "")
        # Stack directory existence check (missing directory)
        if "test -d" in cmd:
            return (1, "", "")
        # Podman inspect for State.Running during delete -> STILL RUNNING
        if "podman inspect" in cmd and "State.Running" in cmd:
            return (0, "true\n", "")
        # If Docker inspect were mistakenly called, Docker would report not found
        if "docker inspect" in cmd:
            return (1, "", "Error: No such container: cv-realm-broker-test")
        # rm -rf
        if "rm -rf" in cmd:
            return (0, "", "")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with patch("hoplyra.realms.ensure_image"):
        broker = deploy_realm_broker(remote, server, listen_port=8443)

    assert broker["status"] == "active"
    stored = get_broker_by_id(broker["id"])
    assert stored is not None
    # Saved runtime must reflect Podman Compose
    assert "podman" in (stored["runtime"] or stored["meta"].get("runtime") or "")

    # Attempt deletion with missing stack dir and running Podman container
    with pytest.raises(RealmError, match="still running in podman"):
        delete_realm_broker(broker["id"], remote)

    # SQLite record must remain with status='error', and rm -rf must NOT have been executed
    after_del = get_broker_by_id(broker["id"])
    assert after_del is not None
    assert after_del["status"] == "error"
    assert not any("rm -rf" in c for c in commands_executed)


def test_delete_when_saved_runtime_unavailable_does_not_accept_other_runtime():
    """If saved runtime (Podman) is unavailable/fails during delete, do not accept Docker's
    'not found' response as absence. The record must be preserved.
    """
    server = create_test_server(name="SavedRuntimeDownVPS", host="192.0.2.111")
    remote = MagicMock()
    remote.target = SimpleNamespace(is_local=False, host="192.0.2.111")

    commands_executed: list[str] = []

    def mock_run(cmd, *args, **kwargs):
        commands_executed.append(cmd)
        if "podman compose version" in cmd or "command -v podman" in cmd:
            return (0, "podman-compose version 1.0.6\n", "")
        if "podman compose" in cmd and "up" in cmd:
            return (0, "", "")
        if "podman inspect" in cmd and "StartedAt" in cmd:
            return (0, 'running "2026-11-27T15:00:00Z"\n', "")
        if "podman logs" in cmd:
            return (0, "2026-11-27T15:00:02Z server listening on :8443\n", "")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with patch("hoplyra.realms.ensure_image"):
        broker = deploy_realm_broker(remote, server, listen_port=8443)

    assert broker["status"] == "active"

    # Now simulate: directory missing, Podman command missing / engine broken
    def mock_run_podman_down(cmd, *args, **kwargs):
        commands_executed.append(cmd)
        if "test -d" in cmd:
            return (1, "", "")
        if "podman inspect" in cmd:
            return (127, "", "bash: podman: command not found")
        # Docker is running and would return container not found
        if "docker inspect" in cmd:
            return (1, "", "Error: No such container")
        if "rm -rf" in cmd:
            return (0, "", "")
        return (0, "", "")

    remote.run.side_effect = mock_run_podman_down

    with pytest.raises(RealmError, match="unavailable on host"):
        delete_realm_broker(broker["id"], remote)

    stored = get_broker_by_id(broker["id"])
    assert stored is not None
    assert stored["status"] == "error"
    assert not any("rm -rf" in c for c in commands_executed)


def test_migrated_record_with_null_runtime_running_in_podman_preserves_record_and_identifies_runtime():
    """Migrated/legacy broker has runtime=NULL. Docker Compose is available on the VPS.
    The stack directory is missing. The container is actually still running in Podman.
    Deletion must detect container in Podman, save runtime to SQLite, block deletion,
    and preserve record with status='error'.
    """
    server = create_test_server(name="MigratedPodmanVPS", host="192.0.2.122")
    remote = MagicMock()
    remote.target = SimpleNamespace(is_local=False, host="192.0.2.122")

    broker_id = str(uuid.uuid4())
    container_name = f"cv-realm-broker-{broker_id}"
    instance_path = f"/opt/hoplyra/realm-broker-{broker_id}"

    # Insert legacy record with runtime=NULL
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO realms_brokers (id, server_id, listen_port, token, domain_or_ip, status, container_name, instance_path, runtime, meta_json, created_at)
            VALUES (?, ?, 8443, 'tok-migrated', '192.0.2.122', 'active', ?, ?, NULL, '{}', datetime('now'))
            """,
            (broker_id, server["id"], container_name, instance_path),
        )

    commands_executed: list[str] = []

    def mock_run(cmd, *args, **kwargs):
        commands_executed.append(cmd)
        # Stack directory missing
        if "test -d" in cmd:
            return (1, "", "")
        # Docker Compose is installed on the VPS
        if "docker compose version" in cmd or "docker info" in cmd:
            return (0, "Docker Compose version v2.20.0\n", "")
        # Podman is also installed
        if "command -v podman" in cmd or "podman --version" in cmd:
            return (0, "podman version 4.5.0\n", "")
        # Podman inspect finds container running!
        if "podman inspect" in cmd:
            if "State.Running" in cmd:
                return (0, "true\n", "")
            if "State.Status" in cmd:
                return (0, "running\n", "")
        # Docker inspect would return not found
        if "docker inspect" in cmd:
            return (1, "", f"Error: No such container: {container_name}")
        if "rm -rf" in cmd:
            return (0, "", "")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with pytest.raises(RealmError, match="still running in podman"):
        delete_realm_broker(broker_id, remote)

    stored = get_broker_by_id(broker_id)
    assert stored is not None
    assert stored["status"] == "error"
    # Runtime must have been identified and persisted in SQLite
    assert "podman" in (stored["runtime"] or "")
    # rm -rf must NOT have been called
    assert not any("rm -rf" in c for c in commands_executed)


def test_migrated_record_with_null_runtime_one_engine_not_found_other_engine_daemon_error():
    """Migrated broker has runtime=NULL. Docker inspect returns not found, but Podman inspect
    returns a daemon error / connection failure. Absence cannot be proved by Docker alone.
    Deletion must be rejected and record preserved.
    """
    server = create_test_server(name="DaemonErrorVPS", host="192.0.2.133")
    remote = MagicMock()
    remote.target = SimpleNamespace(is_local=False, host="192.0.2.133")

    broker_id = str(uuid.uuid4())
    container_name = f"cv-realm-broker-{broker_id}"
    instance_path = f"/opt/hoplyra/realm-broker-{broker_id}"

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO realms_brokers (id, server_id, listen_port, token, domain_or_ip, status, container_name, instance_path, runtime, meta_json, created_at)
            VALUES (?, ?, 8443, 'tok-daemon', '192.0.2.133', 'active', ?, ?, NULL, '{}', datetime('now'))
            """,
            (broker_id, server["id"], container_name, instance_path),
        )

    commands_executed: list[str] = []

    def mock_run(cmd, *args, **kwargs):
        commands_executed.append(cmd)
        if "test -d" in cmd:
            return (1, "", "")
        # Podman inspect fails with daemon error
        if "podman inspect" in cmd:
            return (1, "", "cannot connect to the Podman socket: connection refused")
        # Docker inspect returns not found
        if "docker inspect" in cmd:
            return (1, "", f"Error: No such container: {container_name}")
        if "rm -rf" in cmd:
            return (0, "", "")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with pytest.raises(RealmError, match="cannot confirm container .* absence"):
        delete_realm_broker(broker_id, remote)

    stored = get_broker_by_id(broker_id)
    assert stored is not None
    assert stored["status"] == "error"
    assert stored["runtime"] is None
    assert not any("compose" in c and "down" in c for c in commands_executed)
    assert not any("rm -rf" in c for c in commands_executed)


def test_start_realm_broker_and_tunnel_persists_runtime_to_database():
    """Starting an existing broker or tunnel with runtime=NULL persists used_runtime into SQLite."""
    server = create_test_server(name="StartVPS", host="192.0.2.144")
    remote = MagicMock()
    remote.target = SimpleNamespace(is_local=False, host="192.0.2.144")

    def mock_run(cmd, *args, **kwargs):
        if "docker info" in cmd or "docker compose version" in cmd:
            return (0, "ok\n", "")
        if "podman compose version" in cmd or "command -v podman" in cmd:
            return (0, "ok\n", "")
        if "podman inspect" in cmd:
            return (0, 'running "2026-11-27T15:00:00Z"\n', "")
        if "docker inspect" in cmd:
            return (1, "", "Error: No such container")
        if "logs" in cmd:
            return (0, "2026-11-27T15:00:01Z hysteria realm server listening on :8443\n2026-11-27T15:00:01Z realm registered\n", "")
        return (0, "", "")

    remote.run.side_effect = mock_run

    broker_id = str(uuid.uuid4())
    tunnel_id = str(uuid.uuid4())

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO realms_brokers (id, server_id, listen_port, token, domain_or_ip, status, container_name, instance_path, runtime, meta_json, created_at)
            VALUES (?, ?, 8443, 'tok-start', '192.0.2.144', 'inactive', ?, ?, NULL, '{}', datetime('now'))
            """,
            (broker_id, server["id"], f"cv-realm-broker-{broker_id}", f"/opt/hoplyra/realm-broker-{broker_id}"),
        )
        conn.execute(
            """
            INSERT INTO realms_tunnels (id, server_id, name, realm_name, broker_type, broker_url, auth_password, pin_sha256, status, container_name, instance_path, client_config, runtime, meta_json, created_at)
            VALUES (?, ?, 'T-Start', 'room-start', 'public', 'realm://public@realm.hy2.io', 'pass', '', 'inactive', ?, ?, '', NULL, '{}', datetime('now'))
            """,
            (tunnel_id, server["id"], f"cv-realm-tun-{tunnel_id}", f"/opt/hoplyra/realm-tunnel-{tunnel_id}"),
        )

    with patch("hoplyra.realms.podman_compose_up", return_value="podman compose"):
        start_realm_broker(broker_id, remote)
        start_realm_tunnel(tunnel_id, remote)

    b_after = get_broker_by_id(broker_id)
    t_after = get_tunnel_by_id(tunnel_id)
    assert b_after is not None and b_after["status"] == "active"
    assert b_after["runtime"] == "podman compose"
    assert t_after is not None and t_after["status"] == "active"
    assert t_after["runtime"] == "podman compose"


def test_collision_in_both_docker_and_podman_returns_error_without_changes():
    """Item 1: If container with same name exists in BOTH Podman and Docker,
    delete_realm_broker must raise RealmError without modifying SQLite or executing compose down / rm -rf.
    """
    server = create_test_server(name="CollisionVPS", host="192.0.2.155")
    remote = MagicMock()
    remote.target = SimpleNamespace(is_local=False, host="192.0.2.155")

    broker_id = str(uuid.uuid4())
    container_name = f"cv-realm-broker-{broker_id}"
    instance_path = f"/opt/hoplyra/realm-broker-{broker_id}"

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO realms_brokers (id, server_id, listen_port, token, domain_or_ip, status, container_name, instance_path, runtime, meta_json, created_at)
            VALUES (?, ?, 8443, 'tok-coll', '192.0.2.155', 'active', ?, ?, NULL, '{}', datetime('now'))
            """,
            (broker_id, server["id"], container_name, instance_path),
        )

    commands_executed: list[str] = []

    def mock_run(cmd, *args, **kwargs):
        commands_executed.append(cmd)
        # Both podman and docker inspect report the container exists!
        if "podman inspect" in cmd and "State.Status" in cmd:
            return (0, "exited\n", "")
        if "docker inspect" in cmd and "State.Status" in cmd:
            return (0, "running\n", "")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with pytest.raises(RealmError, match="exists in multiple engines"):
        delete_realm_broker(broker_id, remote)

    # Record must remain completely UNTOUCHED (still 'active', runtime still NULL)
    stored = get_broker_by_id(broker_id)
    assert stored is not None
    assert stored["status"] == "active"
    assert stored["runtime"] is None
    # No compose down or rm -rf executed
    assert not any("compose" in c and "down" in c for c in commands_executed)
    assert not any("rm -rf" in c for c in commands_executed)


def test_start_detects_existing_podman_owner_when_docker_compose_available():
    """Item 2: Starting a legacy broker (runtime=NULL) checks existing container owner first.
    Even if Docker Compose is installed on the host, it selects the existing Podman container,
    not Docker.
    """
    server = create_test_server(name="StartOwnerVPS", host="192.0.2.166")
    remote = MagicMock()
    remote.target = SimpleNamespace(is_local=False, host="192.0.2.166")

    broker_id = str(uuid.uuid4())
    container_name = f"cv-realm-broker-{broker_id}"
    instance_path = f"/opt/hoplyra/realm-broker-{broker_id}"

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO realms_brokers (id, server_id, listen_port, token, domain_or_ip, status, container_name, instance_path, runtime, meta_json, created_at)
            VALUES (?, ?, 8443, 'tok-owner', '192.0.2.166', 'inactive', ?, ?, NULL, '{}', datetime('now'))
            """,
            (broker_id, server["id"], container_name, instance_path),
        )

    passed_runtimes: list[str | None] = []

    def mock_up(runner, workdir, project, runtime=None):
        passed_runtimes.append(runtime)
        return runtime or "docker compose"

    def mock_run(cmd, *args, **kwargs):
        # Docker Compose is installed
        if "docker info" in cmd or "docker compose version" in cmd:
            return (0, "Docker Compose v2\n", "")
        # Podman inspect finds existing container!
        if "podman inspect" in cmd:
            if "StartedAt" in cmd:
                return (0, 'running "2026-11-27T15:00:00Z"\n', "")
            if "State.Status" in cmd:
                return (0, "exited\n", "")

        # Docker does not have this container
        if "docker inspect" in cmd:
            return (1, "", "Error: No such container")
        if "podman compose version" in cmd:
            return (0, "podman-compose 1.0.6\n", "")
        if "logs" in cmd:
            return (0, "2026-11-27T15:00:01Z hysteria realm server listening on :8443\n", "")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with patch("hoplyra.realms.podman_compose_up", side_effect=mock_up):
        start_realm_broker(broker_id, remote)

    # Verify podman_compose_up was called with Podman runtime, not Docker
    assert len(passed_runtimes) == 1
    assert passed_runtimes[0] == "podman compose"

    stored = get_broker_by_id(broker_id)
    assert stored is not None
    assert stored["status"] == "active"
    assert stored["runtime"] == "podman compose"


def test_podman_compose_variant_detection_selects_podman_dash_compose():
    """Item 3: On a host where 'podman compose' is not available, but 'podman-compose' is,
    runtime is detected and saved as 'podman-compose'.
    """
    server = create_test_server(name="DashComposeVPS", host="192.0.2.177")
    remote = MagicMock()
    remote.target = SimpleNamespace(is_local=False, host="192.0.2.177")

    broker_id = str(uuid.uuid4())
    container_name = f"cv-realm-broker-{broker_id}"
    instance_path = f"/opt/hoplyra/realm-broker-{broker_id}"

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO realms_brokers (id, server_id, listen_port, token, domain_or_ip, status, container_name, instance_path, runtime, meta_json, created_at)
            VALUES (?, ?, 8443, 'tok-dash', '192.0.2.177', 'inactive', ?, ?, NULL, '{}', datetime('now'))
            """,
            (broker_id, server["id"], container_name, instance_path),
        )

    passed_runtimes: list[str | None] = []

    def mock_up(runner, workdir, project, runtime=None):
        passed_runtimes.append(runtime)
        return runtime or "podman-compose"

    def mock_run(cmd, *args, **kwargs):
        if "podman inspect" in cmd:
            if "StartedAt" in cmd:
                return (0, 'running "2026-11-27T15:00:00Z"\n', "")
            if "State.Status" in cmd:
                return (0, "exited\n", "")

        if "docker inspect" in cmd:
            return (1, "", "Error: No such container")
        # podman compose fails, but podman-compose succeeds
        if "podman compose version" in cmd:
            return (1, "", "podman: unknown command 'compose'")
        if "command -v podman-compose" in cmd:
            return (0, "/usr/bin/podman-compose\n", "")
        if "logs" in cmd:
            return (0, "2026-11-27T15:00:01Z hysteria realm server listening on :8443\n", "")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with patch("hoplyra.realms.podman_compose_up", side_effect=mock_up):
        start_realm_broker(broker_id, remote)

    # Must have detected and used podman-compose
    assert len(passed_runtimes) == 1
    assert passed_runtimes[0] == "podman-compose"

    stored = get_broker_by_id(broker_id)
    assert stored is not None
    assert stored["status"] == "active"
    assert stored["runtime"] == "podman-compose"


def test_migrated_record_container_found_but_other_engine_permission_denied_rejects_without_changes():
    """Item 1: Container found in Podman, but Docker check fails with permission denied / daemon error.
    Ownership is NOT unambiguous. delete_realm_broker and start_realm_broker must raise RealmError
    without binding runtime to SQLite or calling compose down / rm -rf.
    """
    server = create_test_server(name="PermDeniedVPS", host="192.0.2.188")
    remote = MagicMock()
    remote.target = SimpleNamespace(is_local=False, host="192.0.2.188")

    broker_id = str(uuid.uuid4())
    container_name = f"cv-realm-broker-{broker_id}"
    instance_path = f"/opt/hoplyra/realm-broker-{broker_id}"

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO realms_brokers (id, server_id, listen_port, token, domain_or_ip, status, container_name, instance_path, runtime, meta_json, created_at)
            VALUES (?, ?, 8443, 'tok-perm', '192.0.2.188', 'active', ?, ?, NULL, '{}', datetime('now'))
            """,
            (broker_id, server["id"], container_name, instance_path),
        )

    commands_executed: list[str] = []

    def mock_run(cmd, *args, **kwargs):
        commands_executed.append(cmd)
        # Podman inspect finds container!
        if "podman inspect" in cmd and "State.Status" in cmd:
            return (0, "running\n", "")
        # Docker inspect fails with permission denied
        if "docker inspect" in cmd:
            return (1, "", "permission denied while trying to connect to the Docker daemon socket")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with pytest.raises(RealmError, match="ambiguous ownership"):
        delete_realm_broker(broker_id, remote)

    # Record must be untouched (runtime still NULL, status still active)
    stored = get_broker_by_id(broker_id)
    assert stored is not None
    assert stored["status"] == "active"
    assert stored["runtime"] is None
    # No down or rm -rf
    assert not any("compose" in c and "down" in c for c in commands_executed)
    assert not any("rm -rf" in c for c in commands_executed)


def test_compose_unavailable_raises_realm_error_before_writing_to_db():
    """Item 2: If container engine is identified (e.g. Podman), but no supported compose command
    is accessible on the host, _detect_compose_variant_for_engine raises RealmError and nothing
    is saved to SQLite.
    """
    server = create_test_server(name="NoComposeVPS", host="192.0.2.199")
    remote = MagicMock()
    remote.target = SimpleNamespace(is_local=False, host="192.0.2.199")

    broker_id = str(uuid.uuid4())
    container_name = f"cv-realm-broker-{broker_id}"
    instance_path = f"/opt/hoplyra/realm-broker-{broker_id}"

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO realms_brokers (id, server_id, listen_port, token, domain_or_ip, status, container_name, instance_path, runtime, meta_json, created_at)
            VALUES (?, ?, 8443, 'tok-nocompose', '192.0.2.199', 'inactive', ?, ?, NULL, '{}', datetime('now'))
            """,
            (broker_id, server["id"], container_name, instance_path),
        )

    def mock_run(cmd, *args, **kwargs):
        if "podman inspect" in cmd:
            return (0, "exited\n", "")
        if "docker inspect" in cmd:
            return (1, "", "Error: No such container")
        # Both podman compose and podman-compose fail
        if "podman compose version" in cmd:
            return (1, "", "podman: unknown command 'compose'")
        if "command -v podman-compose" in cmd:
            return (1, "", "podman-compose: not found")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with pytest.raises(RealmError, match="Compose unavailable for Podman"):
        start_realm_broker(broker_id, remote)

    # Must NOT have saved an unconfirmed runtime to SQLite!
    stored = get_broker_by_id(broker_id)
    assert stored is not None
    assert stored["runtime"] is None


def test_docker_compose_unavailable_raises_realm_error():
    """Item 2: Container found in Docker, but 'docker compose version' fails.
    Must raise RealmError and not bind runtime.
    """
    server = create_test_server(name="NoDockerComposeVPS", host="192.0.2.200")
    remote = MagicMock()
    remote.target = SimpleNamespace(is_local=False, host="192.0.2.200")

    broker_id = str(uuid.uuid4())
    container_name = f"cv-realm-broker-{broker_id}"
    instance_path = f"/opt/hoplyra/realm-broker-{broker_id}"

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO realms_brokers (id, server_id, listen_port, token, domain_or_ip, status, container_name, instance_path, runtime, meta_json, created_at)
            VALUES (?, ?, 8443, 'tok-nodockercompose', '192.0.2.200', 'inactive', ?, ?, NULL, '{}', datetime('now'))
            """,
            (broker_id, server["id"], container_name, instance_path),
        )

    def mock_run(cmd, *args, **kwargs):
        if "docker inspect" in cmd:
            return (0, "exited\n", "")
        if "podman inspect" in cmd:
            return (1, "", "Error: No such container")
        if "docker compose version" in cmd:
            return (1, "", "docker: 'compose' is not a docker command")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with pytest.raises(RealmError, match="Compose unavailable for Docker"):
        start_realm_broker(broker_id, remote)

    stored = get_broker_by_id(broker_id)
    assert stored is not None
    assert stored["runtime"] is None


def test_stop_migrated_record_docker_not_found_podman_permission_denied_rejects_without_down():
    """Item High: When stopping a legacy record with runtime=NULL, if Docker inspect returns
    not found, but Podman inspect returns permission denied (or daemon error),
    failed_checks is non-empty while found_engines is empty.
    Operation must be aborted before compose down, and status must NOT become 'inactive'.
    """
    server = create_test_server(name="StopUnknownVPS", host="192.0.2.211")
    remote = MagicMock()
    remote.target = SimpleNamespace(is_local=False, host="192.0.2.211")

    broker_id = str(uuid.uuid4())
    container_name = f"cv-realm-broker-{broker_id}"
    instance_path = f"/opt/hoplyra/realm-broker-{broker_id}"

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO realms_brokers (id, server_id, listen_port, token, domain_or_ip, status, container_name, instance_path, runtime, meta_json, created_at)
            VALUES (?, ?, 8443, 'tok-stop-unknown', '192.0.2.211', 'active', ?, ?, NULL, '{}', datetime('now'))
            """,
            (broker_id, server["id"], container_name, instance_path),
        )

    commands_executed: list[str] = []

    def mock_run(cmd, *args, **kwargs):
        commands_executed.append(cmd)
        if "docker inspect" in cmd:
            return (1, "", f"Error: No such container: {container_name}")
        if "podman inspect" in cmd:
            return (1, "", "permission denied while trying to connect to the Podman socket")
        return (0, "", "")

    remote.run.side_effect = mock_run

    with pytest.raises(RealmError, match="cannot confirm container .* absence"):
        stop_realm_broker(broker_id, remote)

    stored = get_broker_by_id(broker_id)
    assert stored is not None
    assert stored["status"] == "active"
    assert stored["runtime"] is None
    assert not any("compose" in c and "down" in c for c in commands_executed)






