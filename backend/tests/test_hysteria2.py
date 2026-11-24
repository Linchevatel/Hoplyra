from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hoplyra.protocols import Hysteria2Deployer, find_hysteria2_port, get_deployer, get_deployer_for_config
from hoplyra.chains.planner import HopSpec, ExitEndpoint, validate_chain
from hoplyra.chains.deployer import ChainDeployer
from hoplyra.image_catalog import all_images


def runner():
    mock = MagicMock()
    mock.target = SimpleNamespace(is_local=False, host='192.0.2.1')
    mock.run.return_value = (0, '', '')
    return mock


def test_deploy_generates_server_client_and_udp_compose():
    remote = runner()
    with patch('hoplyra.protocols.ensure_image') as image, patch('hoplyra.protocols.podman_compose_up') as up:
        result = Hysteria2Deployer().deploy(remote, '12345678-abcd', '2001:db8::1', resources={'listenPort': 444})
    files = {call.args[0].split('/')[-1]: call.args[1] for call in remote.upload_text.call_args_list}
    assert 'listen: :444' in files['config.yaml']
    assert 'primary: "' + result.meta['password'] + '"' in files['config.yaml']
    assert '/etc/hysteria/server.crt' in files['config.yaml']
    assert 'server.crt' in files
    assert 'server.key' in files
    assert 'rewriteHost: true' in files['config.yaml']
    assert '"444:444/udp"' in files['docker-compose.yml']
    assert 'hoplyra-hysteria2:1' in files['docker-compose.yml']
    assert 'hysteria2://primary%3A' + result.meta['password'] + '@[2001:db8::1]:444/' in result.client_config
    assert 'insecure: true' in result.client_config
    assert 'pinSHA256=' in result.client_config
    assert 'pinSHA256: ' in result.client_config
    assert len(result.meta['pinSHA256']) == 64
    assert result.container_name == 'cv-hy2-12345678'
    assert len(result.meta['password']) >= 22
    image.assert_called_once_with(remote, 'hoplyra-hysteria2:1', 'hysteria2')
    up.assert_called_once_with(remote, result.instance_path, 'cv-hysteria2-12345678')
    assert isinstance(get_deployer('hysteria2'), Hysteria2Deployer)
    assert isinstance(get_deployer_for_config('hysteria2', result.meta), Hysteria2Deployer)
    assert ('hoplyra-hysteria2:1', 'hysteria2') in all_images()


def test_port_search_checks_udp_and_tcp():
    remote = runner()
    remote.run.return_value = (0, 'udp UNCONN 0 0 *:443 *:*\ntcp LISTEN 0 128 [::]:444 [::]:*\n', '')
    assert find_hysteria2_port(remote) == 445


def test_port_search_fails_on_runner_error():
    remote = runner()
    remote.run.return_value = (1, '', 'command not found: ss')
    with pytest.raises(RuntimeError, match='Не удалось получить список занятых портов'):
        find_hysteria2_port(remote)


@pytest.mark.parametrize('tor', [False, True])
def test_chain_entry_uses_socks_bridge_and_blocks_tor_udp(tor):
    remote = runner()
    hop = HopSpec('h', 'hysteria2', 's', '192.0.2.1', 0)
    client, services, files, meta = ChainDeployer('/tmp')._hysteria2_entry_bundle(
        remote, '/tmp/instance', '12345678', hop,
        ExitEndpoint('127.0.0.1', 9050, direct=True) if tor else ExitEndpoint('192.0.2.2', 10808), tor,
    )
    import json
    data = {name: content for name, content, mode in files}
    bridge = json.loads(data['hy2-bridge.json'])
    assert 'type: socks5' in data['config.yaml']
    assert bridge['inbounds'][0]['listen'] == '127.0.0.1'
    assert any('network_mode: host' in service for service in services)
    if tor:
        assert any(rule.get('outboundTag') == 'tor-udp-block' for rule in bridge['routing']['rules'])
        assert 'tor-sidecar/torrc' in data
    assert meta['protocol'] == 'hysteria2'


def test_chain_hysteria_only_allowed_as_entry():
    validate_chain([{'protocol': 'hysteria2', 'serverId': 'a'}, {'protocol': 'tor', 'serverId': 'b'}])
    with pytest.raises(ValueError, match='Hysteria 2'):
        validate_chain([{'protocol': 'xray', 'serverId': 'a'}, {'protocol': 'hysteria2', 'serverId': 'b'}])


def test_hysteria_runtime_health():
    import json
    from hoplyra.health_check import check_runtime
    remote = runner()
    remote.run.side_effect = [(0, 'cv-hy2-12345678\n', ''), (0, 'udp UNCONN 0 0 *:443 *:*\n', '')]
    state, _ = check_runtime({'id': '12345678', 'protocol': 'hysteria2', 'meta_json': json.dumps({'listenPort': 443})}, remote)
    assert state == 'alive'


@pytest.mark.parametrize('same_server', [False, True])
def test_chain_entry_compose_integration(same_server):
    from hoplyra.chains.planner import build_plan
    remote = runner()
    plan = build_plan([
        {'id': 'entry', 'protocol': 'hysteria2', 'serverId': 'a'},
        {'id': 'exit', 'protocol': 'tor', 'serverId': 'a' if same_server else 'b'},
    ], {'a': '192.0.2.1', 'b': '192.0.2.2'})
    deployer = ChainDeployer('/tmp/hoplyra')
    with patch('hoplyra.chains.deployer._ensure_compose_images'), patch('hoplyra.chains.deployer.podman_compose_up'), patch('hoplyra.chains.deployer.ensure_openvpn_image'):
        if same_server:
            client, path, meta = deployer._deploy_single_server_chain(remote, '12345678', plan, plan.hops)
        else:
            client, path, meta = deployer._deploy_entry_server(remote, '12345678', plan, [plan.entry], ExitEndpoint('192.0.2.2', 9050, via_tor=True))
    uploaded = {call.args[0].split('/')[-1]: call.args[1] for call in remote.upload_text.call_args_list}
    assert 'hoplyra-hysteria2:1' in uploaded['docker-compose.yml']
    assert 'hy2-bridge' in uploaded['docker-compose.yml']
    assert 'hysteria2://' in client
    assert meta['listenPort'] == 443
