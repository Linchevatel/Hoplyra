"""Integration tests for Hoplyra system update and safe rollback mechanism."""
import http.server
import io
import json
import multiprocessing
import os
import socketserver
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest
from hoplyra.system_update import apply_update_async, check_for_updates, is_update_in_progress


def _run_apply_update_worker(q):
    """Worker function executed in an isolated process to guarantee deadline/deadlock prevention."""
    mock_payload = {
        "tag_name": "v1.7.0",
        "body": "Mock release notes",
        "published_at": "2026-11-20T00:00:00Z",
        "html_url": "https://github.com/Linchevatel/Hoplyra/releases/tag/v1.7.0",
    }
    raw_json = json.dumps(mock_payload).encode("utf-8")

    class MockResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("urllib.request.urlopen", return_value=MockResponse(raw_json)):
        with patch("subprocess.run") as mock_subrun:
            mock_subrun.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            res = apply_update_async(target_version="v1.7.0")
            q.put(res)


def test_apply_update_async_does_not_deadlock_with_process_timeout():
    """Verify that apply_update_async does not deadlock using an isolated process and strict 5s deadline."""
    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=_run_apply_update_worker, args=(q,))
    p.start()
    p.join(timeout=5.0)

    if p.is_alive():
        p.terminate()
        p.join()
        pytest.fail("apply_update_async deadlocked and did not finish within 5.0 seconds")

    assert p.exitcode == 0
    assert not q.empty()
    res = q.get_nowait()
    assert res["ok"] is True
    assert res["status"] == "updating"
    assert res["targetVersion"] == "v1.7.0"


def _create_test_git_repo(repo_path: Path):
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)

    # Initial commit (v1.0.0) with valid static bundle assets
    (repo_path / "run.py").write_text("# entry v1.0.0\n")
    static_v1 = repo_path / "backend" / "hoplyra" / "static"
    (static_v1 / "assets").mkdir(parents=True)
    (static_v1 / "index.html").write_text("<html>v1.0.0</html>\n")
    (static_v1 / "assets" / "app-v1.js").write_text("console.log('v1.0.0');\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "v1.0.0"], cwd=repo_path, check=True)
    subprocess.run(["git", "tag", "v1.0.0"], cwd=repo_path, check=True)
    v1_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_path, text=True).strip()

    # Second commit (v1.1.0)
    (repo_path / "run.py").write_text("# entry v1.1.0\n")
    static_v2 = repo_path / "backend" / "hoplyra" / "static"
    (static_v2 / "index.html").write_text("<html>v1.1.0</html>\n")
    (static_v2 / "assets" / "app-v2.js").write_text("console.log('v1.1.0');\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "v1.1.0"], cwd=repo_path, check=True)
    subprocess.run(["git", "tag", "v1.1.0"], cwd=repo_path, check=True)
    v11_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_path, text=True).strip()

    # Checkout v1.0.0
    subprocess.run(["git", "checkout", "v1.0.0"], cwd=repo_path, check=True, capture_output=True)
    return v1_commit, v11_commit


def test_script_rollback_verifies_restored_health_and_reverts_commit(tmp_path):
    """Verify that when health check fails on v1.1.0, rollback restores HEAD to v1.0.0 and verifies restored health."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    calls = []

    class RollbackHealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/health":
                calls.append(1)
                # The first 10 probes correspond to the update loop checks (all fail HTTP 503).
                # Probes from 11 onwards correspond to the rollback verification (succeed HTTP 200 with JSON).
                if len(calls) <= 10:
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status":"ok"}')  # HTTP 503 with status:ok must be rejected!
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status": "ok"}')  # JSON with whitespace parsed properly
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), RollbackHealthHandler)
    health_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["HEALTH_CHECK_SLEEP"] = "0.05"
    env["LOCK_FILE"] = str(tmp_path / "test_rollback.lock")
    env["LOG_FILE"] = str(tmp_path / "test_rollback.log")

    try:
        res = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", "python3", str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res.returncode == 1
        current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        assert current_head == v1_commit, f"Expected rollback to {v1_commit}, but got {current_head}"
        log_content = Path(env["LOG_FILE"]).read_text()
        assert "Rollback to " in log_content
        assert "verified and completed" in log_content
    finally:
        server.shutdown()
        server.server_close()


def test_script_rollback_exits_2_if_restored_health_remains_unhealthy(tmp_path):
    """Verify that when restored service remains unhealthy even after rollback, script exits with fatal code 2."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    # Server always returns 200 but invalid JSON status
    class BadStatusHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"degraded"}')

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), BadStatusHandler)
    health_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["HEALTH_CHECK_SLEEP"] = "0.05"
    env["LOCK_FILE"] = str(tmp_path / "test_fatal.lock")
    env["LOG_FILE"] = str(tmp_path / "test_fatal.log")

    try:
        res = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", "python3", str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res.returncode == 2, f"Expected exit code 2, got {res.returncode}"
        log_content = Path(env["LOG_FILE"]).read_text()
        assert "Restored service failed health check" in log_content
        assert "FATAL: One or more rollback operations failed!" in log_content
    finally:
        server.shutdown()
        server.server_close()


def test_script_fails_and_rolls_back_if_cp_fails_with_existing_static(tmp_path):
    """Verify that if destination already has old index.html and old JS file, but copying new JS fails, script rolls back."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    # Destination already has valid v1.0.0 files
    ui_dist = repo / "ui" / "dist"
    ui_dist.mkdir(parents=True)
    (ui_dist / "index.html").write_text("<html>v1.0.0</html>\n")
    (ui_dist / "assets").mkdir()
    old_js = ui_dist / "assets" / "app-v1.js"
    old_js.write_text("console.log('v1.0.0');\n")

    # In v1.1.0, there is a new asset app-v2.js.
    # We make a conflicting non-writable directory named app-v2.js in destination so cp -r fails!
    target_conflict = ui_dist / "assets" / "app-v2.js"
    target_conflict.mkdir()
    (target_conflict / "dummy").write_text("blocker")
    os.chmod(target_conflict, 0o555)

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["LOCK_FILE"] = str(tmp_path / "test_static_cp_fail.lock")
    env["LOG_FILE"] = str(tmp_path / "test_static_cp_fail.log")

    try:
        res = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", "python3", "8787", "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res.returncode != 0
        current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        assert current_head == v1_commit
        log_content = Path(env["LOG_FILE"]).read_text()
        assert "ERROR: Failed to copy static files to ui/dist" in log_content
    finally:
        os.chmod(target_conflict, 0o755)


def test_script_healthy_http_release_stays_installed(tmp_path):
    """Verify that a healthy HTTP service release successfully checks out and stays installed without rollback."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _, v11_commit = _create_test_git_repo(repo)

    class HealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), HealthHandler)
    health_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["LOCK_FILE"] = str(tmp_path / "test_update_http.lock")
    env["LOG_FILE"] = str(tmp_path / "test_update_http.log")

    try:
        res = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", "python3", str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res.returncode == 0, f"Update script failed: {res.stdout}\n{res.stderr}"
        current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        assert current_head == v11_commit, f"Expected HEAD to stay on {v11_commit}, but got {current_head}"
    finally:
        server.shutdown()
        server.server_close()


def test_system_update_status_endpoint(monkeypatch):
    """Verify that /api/system/update/status endpoint returns inProgress and currentVersion."""
    from fastapi.testclient import TestClient
    from hoplyra.main import app

    monkeypatch.setattr("hoplyra.auth.is_authenticated", lambda req: True)
    client = TestClient(app)
    response = client.get("/api/system/update/status")
    assert response.status_code == 200
    data = response.json()
    assert "inProgress" in data
    assert "currentVersion" in data
    assert isinstance(data["inProgress"], bool)
    assert isinstance(data["currentVersion"], str)


def test_script_fails_and_aborts_before_switch_when_pip_install_fails_with_green_health(tmp_path):
    """Verify that when pip install fails in release, update aborts before checkout/switch even if health check is green."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    # In v1.1.0 tag, add an invalid requirements.txt
    subprocess.run(["git", "checkout", "v1.1.0"], cwd=repo, check=True, capture_output=True)
    req_file = repo / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("invalid-package-that-does-not-exist-xyz12345678==99.99.99\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add bad reqs"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", "v1.1.0"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "v1.0.0"], cwd=repo, check=True, capture_output=True)

    # Setup mock active venv in repo/backend/.venv
    venv_dir = repo / "backend" / ".venv"
    subprocess.run(["python3", "-m", "venv", "--without-pip", str(venv_dir)], check=True)
    python_bin = venv_dir / "bin" / "python"

    # Health check server ALWAYS returns HTTP 200 {"status": "ok"}
    class AlwaysGreenHealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), AlwaysGreenHealthHandler)
    health_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["LOCK_FILE"] = str(tmp_path / "test_pip_fail.lock")
    env["LOG_FILE"] = str(tmp_path / "test_pip_fail.log")

    try:
        res = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", str(python_bin), str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res.returncode != 0, "Update must fail when pip install fails"
        current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        assert current_head == v1_commit, f"Repository must remain on {v1_commit}, but was {current_head}"
        log_content = Path(env["LOG_FILE"]).read_text()
        assert "ERROR: Failed to install Python dependencies" in log_content
    finally:
        server.shutdown()
        server.server_close()


def test_script_rollback_restores_python_venv(tmp_path):
    """Verify that rollback restores active Python venv to its pre-update state when service health check fails."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    # Ignore .venv in test repo so git never tracks or removes it
    (repo / ".gitignore").write_text(".venv\n.venv*\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "ignore venv"], cwd=repo, check=True)

    # In v1.1.0, add backend/requirements.txt (empty so staging pip succeeds)
    subprocess.run(["git", "checkout", "v1.1.0"], cwd=repo, check=True, capture_output=True)
    req_file = repo / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("# empty requirements\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add empty reqs"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", "v1.1.0"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "v1.0.0"], cwd=repo, check=True, capture_output=True)

    # Setup mock active venv in repo/backend/.venv with marker file
    venv_dir = repo / "backend" / ".venv"
    subprocess.run(["python3", "-m", "venv", str(venv_dir)], check=True)
    (venv_dir / "v1_original_marker.txt").write_text("v1-original\n")
    python_bin = venv_dir / "bin" / "python"

    calls = []

    class FailingHealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/health":
                calls.append(1)
                # First 10 probes fail (update loop), subsequent succeed (rollback verification)
                if len(calls) <= 10:
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status":"error"}')
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), FailingHealthHandler)
    health_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["HEALTH_CHECK_SLEEP"] = "0.05"
    env["LOCK_FILE"] = str(tmp_path / "test_venv_rollback.lock")
    env["LOG_FILE"] = str(tmp_path / "test_venv_rollback.log")

    try:
        res = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", str(python_bin), str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res.returncode == 1, "Script should exit 1 after rollback"
        current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        assert current_head == v1_commit
        # Verify that original marker exists in restored venv
        assert (venv_dir / "v1_original_marker.txt").exists(), "Original venv marker must be restored on rollback"
        log_content = Path(env["LOG_FILE"]).read_text()
        assert "Restoring previous Python venv" in log_content
    finally:
        server.shutdown()
        server.server_close()


def test_script_aborts_before_pip_when_venv_unrecognized(tmp_path):
    """Verify that update aborts before running pip if active venv cannot be reliably identified."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    subprocess.run(["git", "checkout", "v1.1.0"], cwd=repo, check=True, capture_output=True)
    req_file = repo / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("fastapi>=0.115.0\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add reqs"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", "v1.1.0"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "v1.0.0"], cwd=repo, check=True, capture_output=True)

    # Provide dummy python outside a venv, with no .venv in repo
    dummy_python = tmp_path / "dummy_python"
    dummy_python.write_text("#!/bin/sh\nexit 0\n")
    dummy_python.chmod(0o755)

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["LOCK_FILE"] = str(tmp_path / "test_no_venv.lock")
    env["LOG_FILE"] = str(tmp_path / "test_no_venv.log")

    res = subprocess.run(
        ["bash", str(script_src), str(repo), "v1.1.0", str(dummy_python), "8787", "http"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode != 0
    current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    assert current_head == v1_commit
    log_content = Path(env["LOG_FILE"]).read_text()
    assert "Cannot reliably identify active virtual environment" in log_content


def test_script_rollback_when_checkout_fails_after_venv_switched(tmp_path):
    """Verify that if git checkout fails after venv switch, old venv is restored and repo remains on PREV_REF."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    (repo / ".gitignore").write_text(".venv\n.venv*\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "ignore venv"], cwd=repo, check=True)
    v1_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    subprocess.run(["git", "checkout", "v1.1.0"], cwd=repo, check=True, capture_output=True)
    req_file = repo / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("# empty requirements\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add empty reqs"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", "v1.1.0"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", v1_commit], cwd=repo, check=True, capture_output=True)

    venv_dir = repo / "backend" / ".venv"
    subprocess.run(["python3", "-m", "venv", str(venv_dir)], check=True)
    (venv_dir / "original_checkout_marker.txt").write_text("orig\n")
    python_bin = venv_dir / "bin" / "python"

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["MOCK_FAIL_CHECKOUT"] = "1"
    env["HEALTH_CHECK_SLEEP"] = "0.05"
    env["LOCK_FILE"] = str(tmp_path / "test_checkout_fail.lock")
    env["LOG_FILE"] = str(tmp_path / "test_checkout_fail.log")

    res = subprocess.run(
        ["bash", str(script_src), str(repo), "v1.1.0", str(python_bin), "8787", "http"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode != 0
    current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    assert current_head == v1_commit
    assert (venv_dir / "original_checkout_marker.txt").exists(), "Original venv must be restored after checkout failure"
    log_content = Path(env["LOG_FILE"]).read_text()
    assert "Simulated git checkout failure" in log_content
    assert "Restoring previous Python venv" in log_content


def test_script_rollback_when_second_mv_fails(tmp_path):
    """Verify that if the second mv fails during venv switch, original venv directory is restored from backup."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    (repo / ".gitignore").write_text(".venv\n.venv*\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "ignore venv"], cwd=repo, check=True)
    v1_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    subprocess.run(["git", "checkout", "v1.1.0"], cwd=repo, check=True, capture_output=True)
    req_file = repo / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("# empty requirements\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add empty reqs"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", "v1.1.0"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", v1_commit], cwd=repo, check=True, capture_output=True)

    venv_dir = repo / "backend" / ".venv"
    subprocess.run(["python3", "-m", "venv", str(venv_dir)], check=True)
    (venv_dir / "original_mv_marker.txt").write_text("orig-mv\n")
    python_bin = venv_dir / "bin" / "python"

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["MOCK_FAIL_SECOND_MV"] = "1"
    env["HEALTH_CHECK_SLEEP"] = "0.05"
    env["LOCK_FILE"] = str(tmp_path / "test_second_mv_fail.lock")
    env["LOG_FILE"] = str(tmp_path / "test_second_mv_fail.log")

    res = subprocess.run(
        ["bash", str(script_src), str(repo), "v1.1.0", str(python_bin), "8787", "http"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode != 0
    current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    assert current_head == v1_commit
    assert (venv_dir / "original_mv_marker.txt").exists(), "Original venv must be restored after second mv failure"
    log_content = Path(env["LOG_FILE"]).read_text()
    assert "Simulated second mv failure" in log_content
    assert "Second mv failed; restoring" in log_content


def test_console_script_runs_without_bad_interpreter_after_switch(tmp_path):
    """Verify that console commands in the updated venv run properly through symlink without bad interpreter error."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    (repo / ".gitignore").write_text(".venv\n.venv*\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "ignore venv"], cwd=repo, check=True)

    subprocess.run(["git", "checkout", "v1.1.0"], cwd=repo, check=True, capture_output=True)
    req_file = repo / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("# empty requirements\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add empty reqs"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", "v1.1.0"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "v1.0.0"], cwd=repo, check=True, capture_output=True)

    # Create real venv at repo/backend/.venv using python3 -m venv
    venv_dir = repo / "backend" / ".venv"
    subprocess.run(["python3", "-m", "venv", str(venv_dir)], check=True)

    class AlwaysGreenHealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), AlwaysGreenHealthHandler)
    health_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["MOCK_BUILD_IMAGES"] = "1"
    env["HEALTH_CHECK_SLEEP"] = "0.05"
    env["LOCK_FILE"] = str(tmp_path / "test_console.lock")
    env["LOG_FILE"] = str(tmp_path / "test_console.log")

    try:
        res = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", str(venv_dir / "bin" / "python"), str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res.returncode == 0, f"Update must succeed. Stderr: {res.stderr}\nLog: {Path(env['LOG_FILE']).read_text()}"

        active_pip = venv_dir / "bin" / "pip"
        assert active_pip.exists(), "pip must exist in active venv"
        pip_res = subprocess.run([str(active_pip), "--version"], capture_output=True, text=True)
        assert pip_res.returncode == 0, f"pip failed to run: {pip_res.stderr}"
        assert "python" in pip_res.stdout.lower()
    finally:
        server.shutdown()
        server.server_close()


def test_two_consecutive_updates_with_same_tag_preserve_venv_and_cli(tmp_path):
    """Verify that running two consecutive updates with the same tag does not destroy the active venv and CLI works."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    (repo / ".gitignore").write_text(".venv\n.venv*\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "ignore venv"], cwd=repo, check=True)

    subprocess.run(["git", "checkout", "v1.1.0"], cwd=repo, check=True, capture_output=True)
    req_file = repo / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("# empty requirements\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add empty reqs"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", "v1.1.0"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "v1.0.0"], cwd=repo, check=True, capture_output=True)

    # Initial real venv at repo/backend/.venv
    venv_dir = repo / "backend" / ".venv"
    subprocess.run(["python3", "-m", "venv", str(venv_dir)], check=True)

    class AlwaysGreenHealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), AlwaysGreenHealthHandler)
    health_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["MOCK_BUILD_IMAGES"] = "1"
    env["HEALTH_CHECK_SLEEP"] = "0.05"
    env["LOCK_FILE"] = str(tmp_path / "test_consec.lock")
    env["LOG_FILE"] = str(tmp_path / "test_consec.log")

    try:
        # Run 1: update to v1.1.0
        res1 = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", str(venv_dir / "bin" / "python"), str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res1.returncode == 0, f"First update failed: {res1.stderr}\nLog: {Path(env['LOG_FILE']).read_text()}"
        assert venv_dir.is_symlink(), "Active venv should now be a symlink to release venv"

        # Run 2: repeat update with SAME tag v1.1.0
        res2 = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", str(venv_dir / "bin" / "python"), str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res2.returncode == 0, f"Second update with same tag failed: {res2.stderr}\nLog: {Path(env['LOG_FILE']).read_text()}"
        assert venv_dir.is_symlink(), "Active venv should remain a valid symlink"

        # Verify active Python and CLI work
        py_res = subprocess.run([str(venv_dir / "bin" / "python"), "--version"], capture_output=True, text=True)
        assert py_res.returncode == 0
        pip_res = subprocess.run([str(venv_dir / "bin" / "pip"), "--version"], capture_output=True, text=True)
        assert pip_res.returncode == 0
        assert "python" in pip_res.stdout.lower()
    finally:
        server.shutdown()
        server.server_close()


def test_system_python_with_foreign_repo_venv_aborts_before_pip(tmp_path):
    """Verify that passing system python when a foreign backend/.venv exists aborts before pip without picking it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    (repo / ".gitignore").write_text(".venv\n.venv*\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "ignore venv"], cwd=repo, check=True)
    v1_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    subprocess.run(["git", "checkout", "v1.1.0"], cwd=repo, check=True, capture_output=True)
    req_file = repo / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("fastapi>=0.115.0\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add reqs"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", "v1.1.0"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", v1_commit], cwd=repo, check=True, capture_output=True)

    # Foreign developer venv at repo/backend/.venv with valid pyvenv.cfg
    foreign_venv = repo / "backend" / ".venv"
    subprocess.run(["python3", "-m", "venv", "--without-pip", str(foreign_venv)], check=True)
    (foreign_venv / "foreign_marker.txt").write_text("do-not-touch\n")

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["LOCK_FILE"] = str(tmp_path / "test_foreign_venv.lock")
    env["LOG_FILE"] = str(tmp_path / "test_foreign_venv.log")

    # Pass system python3 (which has sys.prefix == sys.base_prefix)
    res = subprocess.run(
        ["bash", str(script_src), str(repo), "v1.1.0", "python3", "8787", "http"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode != 0
    current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    assert current_head == v1_commit
    log_content = Path(env["LOG_FILE"]).read_text()
    assert "Cannot reliably identify active virtual environment" in log_content
    # Ensure foreign venv was not touched or promoted
    assert (foreign_venv / "foreign_marker.txt").exists()


def test_rollback_restores_venv_even_if_static_restore_fails(tmp_path):
    """Verify that when static bundle restore fails during rollback, venv rollback still executes completely."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    (repo / ".gitignore").write_text(".venv\n.venv*\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "ignore venv"], cwd=repo, check=True)
    v1_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    subprocess.run(["git", "checkout", "v1.1.0"], cwd=repo, check=True, capture_output=True)
    req_file = repo / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("# empty requirements\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add empty reqs"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", "v1.1.0"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", v1_commit], cwd=repo, check=True, capture_output=True)

    venv_dir = repo / "backend" / ".venv"
    subprocess.run(["python3", "-m", "venv", str(venv_dir)], check=True)
    (venv_dir / "v1_static_fail_marker.txt").write_text("keep-this-venv\n")
    python_bin = venv_dir / "bin" / "python"

    # Server fails health check to trigger rollback
    class FailingHealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/health":
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"down"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), FailingHealthHandler)
    health_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["MOCK_FAIL_STATIC_RESTORE"] = "1"
    env["HEALTH_CHECK_SLEEP"] = "0.05"
    env["LOCK_FILE"] = str(tmp_path / "test_static_fail.lock")
    env["LOG_FILE"] = str(tmp_path / "test_static_fail.log")

    try:
        res = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", str(python_bin), str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        # Should exit with code 2 because static bundle restore failed
        assert res.returncode == 2
        current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        assert current_head == v1_commit
        # CRITICAL: venv must be restored despite the static bundle restore failure
        assert (venv_dir / "v1_static_fail_marker.txt").exists(), "Original venv must be restored even if static restore fails"
        log_content = Path(env["LOG_FILE"]).read_text()
        assert "Simulated static bundle restore failure" in log_content
        assert "Restoring previous Python venv" in log_content
    finally:
        server.shutdown()
        server.server_close()


def test_external_shared_venv_survives_successful_update(tmp_path):
    """Verify that an external shared venv not created by the updater is preserved on successful update."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    (repo / ".gitignore").write_text(".venv\n.venv*\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "ignore venv"], cwd=repo, check=True)

    subprocess.run(["git", "checkout", "v1.1.0"], cwd=repo, check=True, capture_output=True)
    req_file = repo / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("# empty requirements\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add empty reqs"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", "v1.1.0"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "v1.0.0"], cwd=repo, check=True, capture_output=True)

    # External shared venv located completely outside repo
    ext_venv = tmp_path / "external_shared_venv"
    subprocess.run(["python3", "-m", "venv", str(ext_venv)], check=True)
    (ext_venv / "important_shared_data.txt").write_text("critical-do-not-delete\n")

    # Symlink repo/backend/.venv -> ext_venv
    venv_link = repo / "backend" / ".venv"
    venv_link.symlink_to(ext_venv)

    class AlwaysGreenHealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), AlwaysGreenHealthHandler)
    health_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["MOCK_BUILD_IMAGES"] = "1"
    env["HEALTH_CHECK_SLEEP"] = "0.05"
    env["LOCK_FILE"] = str(tmp_path / "test_ext_venv.lock")
    env["LOG_FILE"] = str(tmp_path / "test_ext_venv.log")

    try:
        res = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", str(venv_link / "bin" / "python"), str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res.returncode == 0, f"Update failed: {res.stderr}\nLog: {Path(env['LOG_FILE']).read_text()}"
        # CRITICAL: External shared venv must survive intact!
        assert ext_venv.exists(), "External venv directory must not be deleted!"
        assert (ext_venv / "important_shared_data.txt").exists(), "Data in external venv must survive!"
        assert (ext_venv / "important_shared_data.txt").read_text() == "critical-do-not-delete\n"
        log_content = Path(env["LOG_FILE"]).read_text()
        assert "Preserving unmanaged/external target venv" in log_content
    finally:
        server.shutdown()
        server.server_close()


def test_external_shared_venv_with_marker_survives_and_second_symlink_works(tmp_path):
    """Verify that an external shared venv with updater marker survives update and a second symlink still runs Python."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    (repo / ".gitignore").write_text(".venv\n.venv*\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "ignore venv"], cwd=repo, check=True)

    subprocess.run(["git", "checkout", "v1.1.0"], cwd=repo, check=True, capture_output=True)
    req_file = repo / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("# empty requirements\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add empty reqs"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", "v1.1.0"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "v1.0.0"], cwd=repo, check=True, capture_output=True)

    # External shared venv located completely outside repo, marked as managed by an updater
    ext_venv = tmp_path / "shared_managed_venv"
    subprocess.run(["python3", "-m", "venv", str(ext_venv)], check=True)
    (ext_venv / ".hoplyra_updater_managed").write_text("repo_dir=/another/installation\ninstall_id=other-uuid\n")
    (ext_venv / "important_shared_data.txt").write_text("critical-shared-data\n")

    # Primary installation symlink: repo/backend/.venv -> ext_venv
    venv_link = repo / "backend" / ".venv"
    venv_link.symlink_to(ext_venv)

    # Second installation / service symlink pointing to the same shared venv
    second_install_dir = tmp_path / "second_service"
    second_install_dir.mkdir()
    second_link = second_install_dir / ".venv"
    second_link.symlink_to(ext_venv)

    # Verify second symlink runs Python before update
    second_python = second_link / "bin" / "python"
    check_before = subprocess.run([str(second_python), "--version"], capture_output=True, text=True)
    assert check_before.returncode == 0, f"Second python failed before update: {check_before.stderr}"

    class AlwaysGreenHealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), AlwaysGreenHealthHandler)
    health_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["MOCK_BUILD_IMAGES"] = "1"
    env["HEALTH_CHECK_SLEEP"] = "0.05"
    env["LOCK_FILE"] = str(tmp_path / "test_ext_shared_venv.lock")
    env["LOG_FILE"] = str(tmp_path / "test_ext_shared_venv.log")

    try:
        res = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", str(venv_link / "bin" / "python"), str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res.returncode == 0, f"Update failed: {res.stderr}\nLog: {Path(env['LOG_FILE']).read_text()}"
        # CRITICAL: External shared venv must survive intact!
        assert ext_venv.exists(), "External shared venv directory must not be deleted!"
        assert (ext_venv / "important_shared_data.txt").exists(), "Data in shared venv must survive!"
        log_content = Path(env["LOG_FILE"]).read_text()
        assert "Preserving unmanaged/external target venv" in log_content
        assert "exclusive ownership not confirmed" in log_content

        # CRITICAL: Second symlink must still exist and still launch Python!
        assert second_python.exists(), "Second symlink python binary must still exist!"
        check_after = subprocess.run([str(second_python), "--version"], capture_output=True, text=True)
        assert check_after.returncode == 0, f"Second python failed after update: {check_after.stderr}"
    finally:
        server.shutdown()
        server.server_close()


def test_rollback_mv_failure_preserves_both_venvs_without_dangling_link(tmp_path):
    """Verify that if mv fails specifically during rollback, release venv is preserved to avoid dangling symlink."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    (repo / ".gitignore").write_text(".venv\n.venv*\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "ignore venv"], cwd=repo, check=True)
    v1_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    subprocess.run(["git", "checkout", "v1.1.0"], cwd=repo, check=True, capture_output=True)
    req_file = repo / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("# empty requirements\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add empty reqs"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", "v1.1.0"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", v1_commit], cwd=repo, check=True, capture_output=True)

    initial_venv = repo / "backend" / ".venv_app_v1.0.0"
    subprocess.run(["python3", "-m", "venv", str(initial_venv)], check=True)
    venv_dir = repo / "backend" / ".venv"
    venv_dir.symlink_to(initial_venv)
    python_bin = venv_dir / "bin" / "python"

    # Server fails health check to trigger rollback
    class FailingHealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/health":
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"error"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), FailingHealthHandler)
    health_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["MOCK_FAIL_ROLLBACK_MV"] = "1"
    env["HEALTH_CHECK_SLEEP"] = "0.05"
    env["LOCK_FILE"] = str(tmp_path / "test_rollback_mv_fail.lock")
    env["LOG_FILE"] = str(tmp_path / "test_rollback_mv_fail.log")

    try:
        res = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", str(python_bin), str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res.returncode == 2, f"Expected fatal exit code 2, got {res.returncode}"
        # CRITICAL: Active venv must NOT be a dangling link
        active_python = venv_dir / "bin" / "python"
        assert active_python.exists(), "Active Python interpreter must exist and not be a dangling link"
        log_content = Path(env["LOG_FILE"]).read_text()
        assert "Simulated rollback mv failure" in log_content
        assert "preserving" in log_content and "avoid dangling link" in log_content
    finally:
        server.shutdown()
        server.server_close()


def test_internal_shared_venv_with_second_symlink_survives_second_update(tmp_path):
    """Verify that an internal venv created by update 1 survives update 2 so a second service symlink continues working."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    (repo / ".gitignore").write_text(".venv\n.venv*\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "ignore venv"], cwd=repo, check=True)

    subprocess.run(["git", "checkout", "v1.1.0"], cwd=repo, check=True, capture_output=True)
    req_file = repo / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("# empty requirements\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add empty reqs"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", "v1.1.0"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "v1.0.0"], cwd=repo, check=True, capture_output=True)

    venv_dir = repo / "backend" / ".venv"
    subprocess.run(["python3", "-m", "venv", str(venv_dir)], check=True)

    class AlwaysGreenHealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), AlwaysGreenHealthHandler)
    health_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["MOCK_BUILD_IMAGES"] = "1"
    env["HEALTH_CHECK_SLEEP"] = "0.05"
    env["LOCK_FILE"] = str(tmp_path / "test_shared_internal.lock")
    env["LOG_FILE"] = str(tmp_path / "test_shared_internal.log")

    try:
        # Update 1: creates an updater-managed release venv inside repo
        res1 = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", str(venv_dir / "bin" / "python"), str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res1.returncode == 0, f"Update 1 failed: {res1.stderr}"
        first_target = venv_dir.resolve()
        assert first_target.exists()
        assert (first_target / ".hoplyra_updater_managed").exists()

        # Service B points a second symlink to this venv
        service_b_dir = tmp_path / "service_b"
        service_b_dir.mkdir()
        second_venv_link = service_b_dir / ".venv"
        second_venv_link.symlink_to(first_target)
        second_python = second_venv_link / "bin" / "python"
        check_before = subprocess.run([str(second_python), "--version"], capture_output=True, text=True)
        assert check_before.returncode == 0

        # Update 2: update A again
        res2 = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", str(venv_dir / "bin" / "python"), str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res2.returncode == 0, f"Update 2 failed: {res2.stderr}"

        # CRITICAL: first_target must NOT be automatically deleted, and second_python must still run!
        assert first_target.exists(), "Previous target venv must be preserved to prevent breaking other services!"
        check_after = subprocess.run([str(second_python), "--version"], capture_output=True, text=True)
        assert check_after.returncode == 0, f"Second python failed after second update: {check_after.stderr}"

        log_content = Path(env["LOG_FILE"]).read_text()
        assert "Preserving previous target venv" in log_content
        assert "automatic deletion disabled to protect shared references" in log_content
    finally:
        server.shutdown()
        server.server_close()


def test_marker_is_not_overwritten_by_cp_a_during_migration(tmp_path):
    """Verify that cp -a during migration does not overwrite the new release marker with foreign/old metadata."""
    repo = tmp_path / "repo"
    repo.mkdir()
    v1_commit, _ = _create_test_git_repo(repo)

    (repo / ".gitignore").write_text(".venv\n.venv*\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "ignore venv"], cwd=repo, check=True)

    subprocess.run(["git", "checkout", "v1.1.0"], cwd=repo, check=True, capture_output=True)
    req_file = repo / "backend" / "requirements.txt"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req_file.write_text("# empty requirements\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add empty reqs"], cwd=repo, check=True)
    subprocess.run(["git", "tag", "-f", "v1.1.0"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "v1.0.0"], cwd=repo, check=True, capture_output=True)

    # Initial venv with an OLD / foreign marker from another installation
    venv_dir = repo / "backend" / ".venv"
    subprocess.run(["python3", "-m", "venv", str(venv_dir)], check=True)
    old_marker = venv_dir / ".hoplyra_updater_managed"
    old_marker.write_text("repo_dir=/some/foreign/path\ninstall_id=foreign-install-id-999\n")

    class AlwaysGreenHealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), AlwaysGreenHealthHandler)
    health_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    script_src = Path(__file__).resolve().parents[2] / "scripts" / "self_update.sh"
    env = os.environ.copy()
    env["MOCK_SERVICE_RESTART"] = "1"
    env["MOCK_BUILD_IMAGES"] = "1"
    env["HEALTH_CHECK_SLEEP"] = "0.05"
    env["LOCK_FILE"] = str(tmp_path / "test_migration_marker.lock")
    env["LOG_FILE"] = str(tmp_path / "test_migration_marker.log")

    try:
        res = subprocess.run(
            ["bash", str(script_src), str(repo), "v1.1.0", str(venv_dir / "bin" / "python"), str(health_port), "http"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert res.returncode == 0, f"Update failed: {res.stderr}\nLog: {Path(env['LOG_FILE']).read_text()}"

        active_target = venv_dir.resolve()
        new_marker = active_target / ".hoplyra_updater_managed"
        assert new_marker.exists(), "New release venv must have .hoplyra_updater_managed"
        marker_content = new_marker.read_text()

        # Must NOT contain foreign values from old venv
        assert "foreign-install-id-999" not in marker_content
        assert "/some/foreign/path" not in marker_content

        # Must contain current repo's canonical path and install_id
        expected_repo_dir = str(repo.resolve())
        expected_install_id = (repo / ".git" / "hoplyra_install_id").read_text().strip()
        assert f"repo_dir={expected_repo_dir}" in marker_content
        assert f"install_id={expected_install_id}" in marker_content
    finally:
        server.shutdown()
        server.server_close()





