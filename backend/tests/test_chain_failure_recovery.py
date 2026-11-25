"""Offline regressions: real job orchestration/SQLite, mocked remote boundaries only."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from hoplyra import db, deploy_jobs as jobs
from hoplyra.chains.deployer import ChainDeployer
from hoplyra.chains.planner import build_plan


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hoplyra.db")
    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path)
    db.init_db()
    hops = [{"id": f"h{i}", "protocol": "wg", "serverId": sid}
            for i, sid in enumerate(("entry", "relay", "exit"))]
    with db.connect() as conn:
        for i, sid in enumerate(("entry", "relay", "exit", "existing")):
            conn.execute("INSERT INTO servers (id,name,host,port,username,auth_type,created_at) VALUES (?,?,?,22,'root','password',?)",
                         (sid, sid, f"203.0.113.{i+1}", db._now()))
        for cid, sid, status, meta in (
            ("attempt", "entry", "deploying", {"chain": True, "hops": hops, "hopDeployStatus": ["deploying", "waiting", "waiting"]}),
            ("successful", "existing", "active", {"keep": True}),
        ):
            conn.execute("INSERT INTO configs (id,server_id,protocol,status,meta_json,client_config,created_at) VALUES (?,?,'wg',?,?,?,?)",
                         (cid, sid, status, json.dumps(meta), "preserved-client", db._now()))
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected real remote/host operation")
    from hoplyra.remote import RemoteRunner
    monkeypatch.setattr(RemoteRunner, "run", forbidden)
    monkeypatch.setattr(RemoteRunner, "put_text", forbidden) if hasattr(RemoteRunner, "put_text") else None
    return hops


def row(cid="attempt"):
    with db.connect() as conn:
        return dict(conn.execute("SELECT * FROM configs WHERE id=?", (cid,)).fetchone())


def fail_middle(monkeypatch, hops, cleanup):
    plan = build_plan(hops, {sid: f"203.0.113.{i+1}" for i, sid in enumerate(("entry", "relay", "exit"))})
    visited = []
    def exit_ok(*args):
        visited.append("exit")
        return MagicMock()
    def relay_fail(*args):
        visited.append("relay")
        raise RuntimeError("middle node failed")
    monkeypatch.setattr(ChainDeployer, "_deploy_exit_server", exit_ok)
    monkeypatch.setattr(ChainDeployer, "_deploy_relay_server", relay_fail)
    monkeypatch.setattr("hoplyra.chains.deployer.recheck_runners_online", lambda _: None)
    monkeypatch.setattr(jobs, "stop_remote_config", cleanup)
    jobs.run_chain_deploy_job("attempt", hops=hops, server_ids=["entry", "relay", "exit"],
                              runners={sid: MagicMock() for sid in ("entry", "relay", "exit")},
                              plan=plan, get_server_row=MagicMock(), server_target=MagicMock())
    assert visited == ["exit", "relay"]


def test_middle_failure_persists_error_and_preserves_success(state, monkeypatch):
    before = row("successful")
    cleanup = MagicMock()
    fail_middle(monkeypatch, state, cleanup)
    failed = row()
    assert failed["status"] == "error"
    assert failed["client_config"] is None
    meta = json.loads(failed["meta_json"])
    assert "middle node failed" in meta["statusMessage"]
    assert "deployPid" not in meta and "deployStartedAt" not in meta
    assert not jobs.is_deploy_active("attempt")
    assert row("successful") == before
    assert cleanup.call_args.args[0]["id"] == "attempt"
    assert cleanup.call_args.kwargs["purge"] is True


def test_middle_failure_rolls_back_actual_touched_servers(state, monkeypatch):
    cleanup = MagicMock()
    fail_middle(monkeypatch, state, cleanup)
    # Exit completed and relay partially provisioned; entry was never started.
    assert set(cleanup.call_args.kwargs["server_ids"]) == {"exit", "relay"}


def test_cleanup_failure_retries_and_retains_original_error(state, monkeypatch):
    cleanup = MagicMock(side_effect=RuntimeError("cleanup unavailable"))
    fail_middle(monkeypatch, state, cleanup)
    assert cleanup.call_count == 2
    assert row()["status"] == "error"
    message = json.loads(row()["meta_json"])["statusMessage"]
    assert "middle node failed" in message and "cleanup unavailable" in message


def test_recovery_preserves_completed_hops_and_is_idempotent(state, monkeypatch):
    cleanup = MagicMock()
    monkeypatch.setattr(jobs, "stop_remote_config", cleanup)
    meta = {"chain": True, "hops": state, "hopDeployStatus": ["done", "deploying", "waiting"],
            "deployPid": 999999, "deployStartedAt": "old"}
    with db.connect() as conn:
        conn.execute("UPDATE configs SET meta_json=? WHERE id='attempt'", (json.dumps(meta),))
    jobs.register_active_deploy("attempt")
    try:
        jobs.recover_stuck_deploying()
        assert row()["status"] == "deploying"
    finally:
        jobs.unregister_active_deploy("attempt")
    jobs.recover_stuck_deploying()
    recovered = row()
    jobs.recover_stuck_deploying()
    assert row() == recovered
    meta = json.loads(recovered["meta_json"])
    assert recovered["status"] == "error"
    assert meta["hopDeployStatus"] == ["done", "error", "waiting"]
    assert "interrupted" in meta["statusMessage"].lower()
    assert "deployPid" not in meta
    cleanup.assert_not_called()


def test_repeat_request_before_worker_starts_is_rejected(state, monkeypatch):
    from fastapi import HTTPException
    from hoplyra import main
    from hoplyra.main import DeployChainRequest
    monkeypatch.setattr(main, "_server_target", lambda row: MagicMock())
    monkeypatch.setattr(main, "verify_chain_servers", lambda *a, **k: {})
    monkeypatch.setattr(main, "recheck_runners_online", lambda *a: None)
    queued = MagicMock()
    monkeypatch.setattr(main, "start_chain_deploy_job", queued)
    # Remove seeded attempt: two sequential requests while the worker is queued.
    with db.connect() as conn:
        conn.execute("DELETE FROM configs WHERE id='attempt'")
    body = DeployChainRequest(hops=state)
    first = main.deploy_chain_config(body)
    with pytest.raises(HTTPException) as exc:
        main.deploy_chain_config(body)
    assert exc.value.status_code == 409
    assert queued.call_count == 1
    assert row(first["id"])["status"] == "deploying"
    assert json.loads(row(first["id"])["meta_json"])["touchedServerIds"] == []


def test_explicit_empty_ownership_skips_cleanup(state, monkeypatch):
    cleanup = MagicMock()
    monkeypatch.setattr(jobs, "stop_remote_config", cleanup)
    cfg = row()
    meta = json.loads(cfg["meta_json"])
    meta["touchedServerIds"] = []
    cfg["meta_json"] = json.dumps(meta)
    assert jobs._touched_chain_server_ids(cfg) == []
    assert jobs._rollback_deploy_artifacts(cfg, get_server_row=MagicMock(), server_target=MagicMock()) is None
    cleanup.assert_not_called()


def test_legacy_ownership_falls_back_to_all_servers(state, monkeypatch):
    cleanup = MagicMock()
    monkeypatch.setattr(jobs, "stop_remote_config", cleanup)
    jobs._rollback_deploy_artifacts(row(), get_server_row=MagicMock(), server_target=MagicMock())
    assert cleanup.call_args.kwargs["server_ids"] is None
    assert jobs._touched_chain_server_ids({"meta_json": "{}"}) is None


@pytest.mark.parametrize("awg_prefetch", [False, True])
def test_first_provisioning_failure_records_ownership(state, monkeypatch, awg_prefetch):
    hops = [dict(h) for h in state]
    if awg_prefetch:
        hops[0]["protocol"] = "awg"
    plan = build_plan(hops, {sid: f"203.0.113.{i+1}" for i, sid in enumerate(("entry", "relay", "exit"))})
    expected = "entry" if awg_prefetch else "exit"
    def fail(*args):
        assert json.loads(row()["meta_json"])["touchedServerIds"] == [expected]
        raise RuntimeError("early provisioning failed")
    monkeypatch.setattr(ChainDeployer, "_deploy_entry_awg_only" if awg_prefetch else "_deploy_exit_server", fail)
    monkeypatch.setattr("hoplyra.chains.deployer.recheck_runners_online", lambda _: None)
    cleanup = MagicMock()
    monkeypatch.setattr(jobs, "stop_remote_config", cleanup)
    jobs.run_chain_deploy_job("attempt", hops=hops, server_ids=list(plan.servers_order),
                              runners={sid: MagicMock() for sid in plan.servers_order},
                              plan=plan, get_server_row=MagicMock(), server_target=MagicMock())
    assert cleanup.call_args.kwargs["server_ids"] == [expected]
    assert "early provisioning failed" in json.loads(row()["meta_json"])["statusMessage"]


def test_failure_before_provisioning_keeps_empty_ownership(state, monkeypatch):
    plan = build_plan(state, {sid: sid for sid in ("entry", "relay", "exit")})
    monkeypatch.setattr("hoplyra.chains.deployer.recheck_runners_online", MagicMock(side_effect=RuntimeError("offline")))
    cleanup = MagicMock()
    monkeypatch.setattr(jobs, "stop_remote_config", cleanup)
    jobs.run_chain_deploy_job("attempt", hops=state, server_ids=list(plan.servers_order),
                              runners={sid: MagicMock() for sid in plan.servers_order},
                              plan=plan, get_server_row=MagicMock(), server_target=MagicMock())
    assert json.loads(row()["meta_json"])["touchedServerIds"] == []
    cleanup.assert_not_called()


def test_wg_link_endpoints_owned_before_link_failure(state, monkeypatch):
    hops = [dict(state[0], protocol="openvpn"), dict(state[-1])]
    plan = build_plan(hops, {"entry": "203.0.113.1", "exit": "203.0.113.3"})
    from hoplyra.chains.planner import ExitEndpoint
    monkeypatch.setattr("hoplyra.chains.deployer.recheck_runners_online", lambda _: None)
    monkeypatch.setattr(ChainDeployer, "_deploy_exit_server", lambda *a: ExitEndpoint("203.0.113.3", 55424, link_client_conf="peer", link_protocol="awg"))
    def fail(*args, **kwargs):
        assert set(json.loads(row()["meta_json"])["touchedServerIds"]) == {"entry", "exit"}
        raise RuntimeError("link failed")
    monkeypatch.setattr(ChainDeployer, "_deploy_exit_link", fail)
    monkeypatch.setattr(ChainDeployer, "_instance", lambda *a: "/tmp/chain")
    monkeypatch.setattr("hoplyra.chains.deployer._host_path_suffix", lambda *a: "/entry")
    cleanup = MagicMock()
    monkeypatch.setattr(jobs, "stop_remote_config", cleanup)
    jobs.run_chain_deploy_job("attempt", hops=hops, server_ids=list(plan.servers_order),
                              runners={sid: MagicMock() for sid in plan.servers_order},
                              plan=plan, get_server_row=MagicMock(), server_target=MagicMock())
    assert set(cleanup.call_args.kwargs["server_ids"]) == {"entry", "exit"}
    assert "link failed" in json.loads(row()["meta_json"])["statusMessage"]


def test_thread_start_failure_releases_reservation_and_pending_row(state, monkeypatch):
    from hoplyra import main
    monkeypatch.setattr(main, '_server_target', lambda row: MagicMock())
    monkeypatch.setattr(main, 'verify_chain_servers', lambda *a, **k: {})
    monkeypatch.setattr(main, 'recheck_runners_online', lambda *a: None)
    with db.connect() as conn:
        conn.execute("DELETE FROM configs WHERE id='attempt'")
    monkeypatch.setattr(main, 'new_id', lambda: 'queued-failure')
    before = list(jobs._ACTIVE_THREADS)
    def fail_start(self):
        assert jobs.is_deploy_active('queued-failure')
        assert row('queued-failure')['status'] == 'deploying'
        raise RuntimeError('start failed')
    monkeypatch.setattr(jobs.threading.Thread, 'start', fail_start)
    with pytest.raises(RuntimeError, match='start failed'):
        main.deploy_chain_config(main.DeployChainRequest(hops=state))
    assert not jobs.is_deploy_active('queued-failure')
    assert jobs._ACTIVE_THREADS == before
    with db.connect() as conn:
        assert conn.execute("SELECT id FROM configs WHERE id='queued-failure'").fetchone() is None


def test_transaction_failure_releases_reservation(state, monkeypatch):
    from hoplyra import main
    from fastapi import HTTPException
    monkeypatch.setattr(main, '_server_target', lambda row: MagicMock())
    monkeypatch.setattr(main, 'verify_chain_servers', lambda *a, **k: {})
    monkeypatch.setattr(main, 'recheck_runners_online', lambda *a: None)
    monkeypatch.setattr(main, 'new_id', lambda: 'conflicting')
    start = MagicMock()
    monkeypatch.setattr(main, 'start_chain_deploy_job', start)
    with pytest.raises(HTTPException) as exc:
        main.deploy_chain_config(main.DeployChainRequest(hops=state))
    assert exc.value.status_code == 409
    assert not jobs.is_deploy_active('conflicting')
    assert row()['status'] == 'deploying'  # SQLite ownership, no active registration
    start.assert_not_called()


def test_startup_snapshot_recovers_old_but_not_new_pending(state, monkeypatch):
    captured = []
    class QueuedThread:
        def __init__(self, *, target, **kwargs):
            captured.append(target)
        def start(self):
            pass
    monkeypatch.setattr(jobs.threading, 'Thread', QueuedThread)
    monkeypatch.setattr(jobs.time, 'sleep', lambda _: None)
    jobs.schedule_recover_stuck_deploying()
    with db.connect() as conn:
        conn.execute("INSERT INTO configs (id,server_id,protocol,status,meta_json,created_at) VALUES ('new-pending','relay','wg','deploying','{}',?)", (db._now(),))
    captured[0]()
    assert row()['status'] == 'error'
    assert row('new-pending')['status'] == 'deploying'


def test_concurrent_requests_atomically_reserve_overlapping_servers(state, monkeypatch):
    import threading
    from fastapi import HTTPException
    from hoplyra import main
    with db.connect() as conn:
        conn.execute("DELETE FROM configs WHERE id='attempt'")
    barrier = threading.Barrier(2)
    monkeypatch.setattr(main, '_server_target', lambda row: MagicMock())
    monkeypatch.setattr(main, 'verify_chain_servers', lambda *a, **k: {})
    monkeypatch.setattr(main, 'recheck_runners_online', lambda *a: barrier.wait(timeout=5))
    queued = MagicMock()
    monkeypatch.setattr(main, 'start_chain_deploy_job', queued)
    results, errors = [], []
    def request(hops):
        try:
            results.append(main.deploy_chain_config(main.DeployChainRequest(hops=hops)))
        except Exception as exc:
            errors.append(exc)
    # Different entry servers but overlapping relay/exit: meta references must lock too.
    other = [dict(h, serverId='existing') if i == 0 else dict(h) for i, h in enumerate(state)]
    # Remove the unrelated active fixture so both requests are initially eligible.
    with db.connect() as conn:
        conn.execute("DELETE FROM configs WHERE id='successful'")
    threads = [threading.Thread(target=request, args=(hops,)) for hops in (state, other)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert all(not thread.is_alive() for thread in threads)
        assert len(results) == 1
        assert len(errors) == 1 and isinstance(errors[0], HTTPException)
        assert errors[0].status_code == 409
        assert queued.call_count == 1
        with db.connect() as conn:
            assert conn.execute("SELECT count(*) FROM configs WHERE status='deploying'").fetchone()[0] == 1
    finally:
        for result in results:
            jobs.unregister_active_deploy(result['id'])


def test_stamp_failure_releases_active_registration(state, monkeypatch):
    def fail_stamp(config_id):
        raise RuntimeError('stamp failed')
    monkeypatch.setattr(jobs, '_stamp_deploy_job', fail_stamp)
    runner = MagicMock()
    with pytest.raises(RuntimeError, match='stamp failed'):
        jobs._run_deploy_job('attempt', runner)
    assert not jobs.is_deploy_active('attempt')
    runner.assert_not_called()


def test_insert_failure_releases_reservation(state, monkeypatch):
    import sqlite3
    from hoplyra import main
    monkeypatch.setattr(main, '_server_target', lambda row: MagicMock())
    monkeypatch.setattr(main, 'verify_chain_servers', lambda *a, **k: {})
    monkeypatch.setattr(main, 'recheck_runners_online', lambda *a: None)
    monkeypatch.setattr(main, 'new_id', lambda: 'insert-failure')
    start = MagicMock()
    monkeypatch.setattr(main, 'start_chain_deploy_job', start)
    with db.connect() as conn:
        conn.execute("DELETE FROM configs WHERE id='attempt'")
        conn.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON configs BEGIN SELECT RAISE(ABORT, 'insert failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match='insert failed'):
        main.deploy_chain_config(main.DeployChainRequest(hops=state))
    assert not jobs.is_deploy_active('insert-failure')
    with db.connect() as conn:
        assert conn.execute("SELECT id FROM configs WHERE id='insert-failure'").fetchone() is None
    start.assert_not_called()


def test_single_request_rejects_unregistered_chain_pending_server(state, monkeypatch):
    from fastapi import HTTPException
    from hoplyra import main
    monkeypatch.setattr(main, '_server_target', lambda row: MagicMock())
    monkeypatch.setattr(main, 'test_ssh_auth', lambda target: (True, None))
    monkeypatch.setattr(main, 'RemoteRunner', MagicMock(return_value=MagicMock(run=MagicMock(return_value=(0, '', '')))))
    monkeypatch.setattr(main, 'ensure_host_ready', lambda runner: None)
    start = MagicMock()
    monkeypatch.setattr(main, 'start_single_deploy_job', start)
    assert not jobs.is_deploy_active('attempt')
    with pytest.raises(HTTPException) as exc:
        main.deploy_config(main.DeployRequest(serverId='relay', protocol='wg'))
    assert exc.value.status_code == 409
    assert row()['status'] == 'deploying'
    start.assert_not_called()


def test_initial_ownership_write_failure_skips_cleanup(state, monkeypatch):
    # Legacy-shaped direct invocation: reject only initialization, allowing the
    # ordinary error report to persist. This is a real SQLite write failure.
    assert 'touchedServerIds' not in json.loads(row()['meta_json'])
    with db.connect() as conn:
        conn.execute("CREATE TRIGGER reject_ownership BEFORE UPDATE ON configs WHEN NEW.meta_json LIKE '%touchedServerIds%' BEGIN SELECT RAISE(ABORT, 'ownership write failed'); END")
    cleanup, deploy = MagicMock(), MagicMock()
    before = row('successful')
    monkeypatch.setattr(jobs, 'stop_remote_config', cleanup)
    monkeypatch.setattr(jobs.ChainDeployer, 'deploy', deploy)
    jobs.run_chain_deploy_job('attempt', hops=state, server_ids=['entry', 'relay', 'exit'],
                              runners={}, plan=None, get_server_row=MagicMock(), server_target=MagicMock())
    deploy.assert_not_called()
    assert row()['status'] == 'error'
    assert 'ownership write failed' in json.loads(row()['meta_json'])['statusMessage']
    assert not jobs.is_deploy_active('attempt')
    assert row('successful') == before
    cleanup.assert_not_called()


def test_first_touch_write_failure_cleans_only_touched_server(state, monkeypatch):
    # Initialization succeeds; the first touch is known locally even when its
    # persistence fails, so cleanup must target exit rather than all servers.
    with db.connect() as conn:
        conn.execute("CREATE TRIGGER reject_touch BEFORE UPDATE ON configs WHEN NEW.meta_json LIKE '%\"touchedServerIds\": [\"exit\"]%' BEGIN SELECT RAISE(ABORT, 'touch write failed'); END")
    plan = build_plan(state, {sid: sid for sid in ('entry', 'relay', 'exit')})
    monkeypatch.setattr('hoplyra.chains.deployer.recheck_runners_online', lambda _: None)
    provision, cleanup = MagicMock(), MagicMock()
    monkeypatch.setattr(ChainDeployer, '_deploy_exit_server', provision)
    monkeypatch.setattr(jobs, 'stop_remote_config', cleanup)
    jobs.run_chain_deploy_job('attempt', hops=state, server_ids=list(plan.servers_order),
                              runners={sid: MagicMock() for sid in plan.servers_order},
                              plan=plan, get_server_row=MagicMock(), server_target=MagicMock())
    provision.assert_not_called()
    assert row()['status'] == 'error'
    assert 'touch write failed' in json.loads(row()['meta_json'])['statusMessage']
    cleanup.assert_called_once()
    assert cleanup.call_args.kwargs['server_ids'] == ['exit']
    assert not jobs.is_deploy_active('attempt')


@pytest.mark.parametrize('admitted_kind', ['single', 'chain'])
@pytest.mark.parametrize('remote_fails', [False, True])
def test_real_worker_handoff_reserves_until_completion(state, monkeypatch, admitted_kind, remote_fails):
    import threading
    from types import SimpleNamespace
    from fastapi import HTTPException
    from hoplyra import main

    entered, release = threading.Event(), threading.Event()
    workers, worker_errors, calls = [], [], []
    stamp = jobs._stamp_deploy_job
    before_threads = list(jobs._ACTIVE_THREADS)
    before_active = set(jobs._ACTIVE_CONFIG_IDS)
    before_success = row('successful')
    with db.connect() as conn:
        conn.execute("DELETE FROM configs WHERE id='attempt'")
    monkeypatch.setattr(main, '_server_target', lambda row: MagicMock())
    monkeypatch.setattr(main, 'verify_chain_servers', lambda *a, **k: {})
    monkeypatch.setattr(main, 'recheck_runners_online', lambda *a: None)
    monkeypatch.setattr(main, 'test_ssh_auth', lambda target: (True, None))
    monkeypatch.setattr(main, 'RemoteRunner', MagicMock(return_value=MagicMock(run=MagicMock(return_value=(0, '', '')))))
    monkeypatch.setattr(main, 'ensure_host_ready', lambda runner: None)
    monkeypatch.setattr(jobs, 'RemoteRunner', MagicMock())
    cleanup = MagicMock()
    monkeypatch.setattr(jobs, 'stop_remote_config', cleanup)

    def paused_stamp(config_id):
        workers.append(threading.current_thread())
        entered.set()
        try:
            assert release.wait(timeout=5), 'worker release timed out'
            stamp(config_id)
        except BaseException as exc:
            worker_errors.append(exc)
            raise
    monkeypatch.setattr(jobs, '_stamp_deploy_job', paused_stamp)

    result = SimpleNamespace(client_config='mock-client', container_name='mock-container',
                             instance_path='mock-instance', meta={}, bypass_meta={})
    def remote(*args, **kwargs):
        calls.append(admitted_kind)
        if admitted_kind == 'chain':
            kwargs['on_server_touched']('exit')
        if remote_fails:
            raise RuntimeError('mock remote failure')
        return result
    monkeypatch.setattr(jobs, 'get_deployer', lambda *a, **k: SimpleNamespace(deploy=remote))
    monkeypatch.setattr(ChainDeployer, 'deploy', remote)
    chain = main.DeployChainRequest(hops=state)
    single = main.DeployRequest(serverId='relay', protocol='wg')
    admitted = None
    try:
        admitted = (main.deploy_chain_config(chain) if admitted_kind == 'chain'
                    else main.deploy_config(single))
        cid = admitted['id']
        assert entered.wait(timeout=5), 'worker did not reach pre-stamp pause'
        assert len(workers) == 1 and workers[0].is_alive()
        assert workers[0] in jobs._ACTIVE_THREADS
        assert jobs.is_deploy_active(cid)
        assert row(cid)['status'] == 'deploying'
        assert 'deployPid' not in json.loads(row(cid)['meta_json'])
        assert calls == []
        # Both API admission paths must reject the same pending ownership.
        for request, body in ((main.deploy_config, single), (main.deploy_chain_config, chain)):
            if admitted_kind == "single" and request == main.deploy_config:
                continue  # Standalone reservations permit parallel deployments.
            with pytest.raises(HTTPException) as exc:
                request(body)
            assert exc.value.status_code == 409
        with db.connect() as conn:
            assert conn.execute("SELECT count(*) FROM configs WHERE id != 'successful'").fetchone()[0] == 1
        assert set(jobs._ACTIVE_CONFIG_IDS) == before_active | {cid}
        assert list(jobs._ACTIVE_THREADS) == before_threads + workers
        release.set()
        workers[0].join(timeout=5)
        assert not workers[0].is_alive()
        assert not worker_errors
        assert calls == [admitted_kind]
        assert row(cid)['status'] == ('error' if remote_fails else 'active')
        assert row('successful') == before_success
        assert not jobs.is_deploy_active(cid)
        assert set(jobs._ACTIVE_CONFIG_IDS) == before_active
        assert list(jobs._ACTIVE_THREADS) == before_threads
        if admitted_kind == 'chain':
            assert json.loads(row(cid)['meta_json'])['touchedServerIds'] == ['exit']
        if remote_fails:
            cleanup.assert_called_once()
            assert cleanup.call_args.args[0]['id'] == cid
            assert cleanup.call_args.kwargs['server_ids'] == (['exit'] if admitted_kind == 'chain' else None)
        else:
            cleanup.assert_not_called()
    finally:
        release.set()
        # Include a started worker even if an assertion failed before stamp entry.
        pending = list(workers) + [t for t in jobs._ACTIVE_THREADS if t not in before_threads]
        for thread in set(pending):
            thread.join(timeout=6)
        assert all(not thread.is_alive() for thread in pending), 'worker leaked'
