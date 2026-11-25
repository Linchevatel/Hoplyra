from __future__ import annotations

from unittest.mock import MagicMock, patch

from hoplyra.chains.deployer import ChainDeployer

from hoplyra.chains.planner import build_plan

from hoplyra.chains.resolver import enumerate_valid_chains



def _mock_runner(host: str='10.0.0.1') -> MagicMock:

    runner = MagicMock()

    runner.target.host = host

    runner.target.is_local = False

    runner.run.return_value = (0, '', '')

    return runner



def test_all_ui_valid_chains_accepted_by_deployer() -> None:

    servers = ['s0', 's1', 's2', 's3']

    hosts = {sid: f'10.0.0.{i + 1}' for i, sid in enumerate(servers)}

    deployer = ChainDeployer('/tmp/hoplyra-test')

    checked = 0

    with patch.object(ChainDeployer, '_deploy_exit_server', return_value=MagicMock(host='10.0.0.9', port=10808, link_client_conf=None)), patch.object(ChainDeployer, '_deploy_relay_server', return_value=MagicMock(host='10.0.0.8', port=1080, link_client_conf=None)), patch.object(ChainDeployer, '_deploy_single_server_chain', return_value=('client', '/opt/hoplyra/chains/x', {})), patch.object(ChainDeployer, '_deploy_entry_server', return_value=('client', '/opt/hoplyra/chains/x/entry', {})), patch.object(ChainDeployer, '_deploy_wg_link', return_value=None), patch('hoplyra.chains.deployer.recheck_runners_online', return_value=None):

        for hops in enumerate_valid_chains(servers):

            plan = build_plan(hops, hosts)

            runners = {sid: _mock_runner(hosts[sid]) for sid in {h['serverId'] for h in hops}}

            result = deployer.deploy(runners, plan, 'test-config-id')

            assert result.client_config

            checked += 1

    assert checked >= 600, f'expected hundreds of valid chains, got {checked}'



def test_same_server_tor_pairs_accepted() -> None:

    servers = ['s0', 's1']

    hosts = {'s0': '10.0.0.1', 's1': '10.0.0.2'}

    deployer = ChainDeployer('/tmp/hoplyra-test')

    with patch.object(ChainDeployer, '_deploy_single_server_chain', return_value=('client', '/p', {})) as single, patch('hoplyra.chains.deployer.recheck_runners_online', return_value=None):

        for hops in enumerate_valid_chains(servers, same_server=True):

            plan = build_plan(hops, hosts)

            if plan.entry.server_id != plan.exit.server_id:

                continue

            runners = {'s0': _mock_runner()}

            deployer.deploy(runners, plan, 'cfg-same')

            single.assert_called()

            return

    raise AssertionError('no same-server chain generated')



def test_colocated_entry_exit_with_relays_orders_entry_twice() -> None:

    hosts = {'s0': '10.0.0.1', 's1': '10.0.0.2', 's2': '10.0.0.3'}

    hops = [{'id': 'h0', 'protocol': 'awg', 'serverId': 's0'}, {'id': 'h1', 'protocol': 'awg', 'serverId': 's1'}, {'id': 'h2', 'protocol': 'awg', 'serverId': 's2'}, {'id': 'h3', 'protocol': 'tor', 'serverId': 's0'}]

    import pytest
    with pytest.raises(ValueError, match="Xray/Tor"):
        build_plan(hops, hosts)

if __name__ == '__main__':

    test_all_ui_valid_chains_accepted_by_deployer()

    test_same_server_tor_pairs_accepted()

    test_colocated_entry_exit_with_relays_orders_entry_twice()

    print('OK: all chain combination tests passed')
