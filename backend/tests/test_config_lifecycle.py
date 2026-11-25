from __future__ import annotations

from unittest.mock import MagicMock, patch

from hoplyra.config_lifecycle import stop_remote_config


def test_stop_single_deploy_without_instance_path() -> None:
    cfg = {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "server_id": "srv-1",
        "protocol": "wg",
        "meta_json": "{}",
    }
    row = {"id": "srv-1", "host": "203.0.113.10", "port": 22, "username": "root", "auth_secret": "x"}
    target = MagicMock()
    runner = MagicMock()
    runner.run.return_value = (1, "", "")
    deployer = MagicMock()

    with patch("hoplyra.config_lifecycle.RemoteRunner", return_value=runner), patch(
        "hoplyra.config_lifecycle.get_deployer_for_config",
        return_value=deployer,
    ), patch("hoplyra.config_lifecycle.is_local_host", return_value=False), patch(
        "hoplyra.config_lifecycle.purge_host_artifacts",
    ):
        stop_remote_config(
            cfg,
            get_server_row=lambda sid: row,
            server_target=lambda r: target,
            purge=True,
        )

    expected_path = "/opt/hoplyra/instances/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    deployer.stop.assert_called_once_with(runner, cfg["id"], expected_path)
    assert runner.run.call_count >= 2


def _stop_awg_chain(runner, *, shared=False, confs=()):
    import json
    cfg = {"id": "chain-owned", "protocol": "awg", "meta_json": json.dumps({
        "chain": True, "hops": [{"protocol": "awg", "serverId": "srv-1"}]
    })}
    with patch("hoplyra.config_lifecycle.RemoteRunner", return_value=runner), patch(
        "hoplyra.config_lifecycle.discover_awg_confs", return_value=list(confs)
    ), patch("hoplyra.config_lifecycle.has_other_config_containers", return_value=shared):
        stop_remote_config(cfg, get_server_row=lambda sid: {}, server_target=lambda row: MagicMock(), purge=True)


def test_chain_owned_awg_down_before_purge_on_shared_host():
    runner = MagicMock()
    runner.run.return_value = (0, "", "")
    confs = ["/opt/hoplyra/chains/chain-owned/hop-0/entry-host/awg0.conf",
             "/opt/hoplyra/chains/chain-owned/hop-2/exit-host/awg0.conf"]
    _stop_awg_chain(runner, shared=True, confs=confs)
    commands = [call.args[0] for call in runner.run.call_args_list]
    first_rm = next(i for i, cmd in enumerate(commands) if "rm -rf" in cmd)
    for conf in confs:
        assert any(f"awg-quick down {conf}" in cmd for cmd in commands[:first_rm])


def test_chain_missing_awg_conf_deletes_link_before_purge():
    runner = MagicMock()
    runner.run.return_value = (0, "", "")
    _stop_awg_chain(runner)
    commands = [call.args[0] for call in runner.run.call_args_list]
    assert commands.index("ip link delete awg0 2>/dev/null || true") < next(
        i for i, cmd in enumerate(commands) if "rm -rf" in cmd
    )


def test_chain_missing_conf_preserves_unowned_shared_awg0():
    runner = MagicMock()
    runner.run.return_value = (0, "", "")
    _stop_awg_chain(runner, shared=True)
    assert not any("ip link delete awg0 " in call.args[0] for call in runner.run.call_args_list)


def test_awg_down_falls_back_to_conf_interface():
    from hoplyra.awg_runtime import awg_quick_down
    runner = MagicMock()
    awg_quick_down(runner, "/missing/awg_owned.conf")
    command = runner.run.call_args.args[0]
    assert "PostDown" in command  # host may lack awg-quick: PostDown runs manually
    assert "s|%i|awg_owned|g" in command
    assert "awg-quick down /missing/awg_owned.conf" in command  # native teardown first
    assert command.index("awg-quick down") < command.index("PostDown")  # manual cleanup only as fallback
    assert "ip link delete awg_owned" in command
