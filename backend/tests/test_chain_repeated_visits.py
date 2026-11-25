import json
from unittest.mock import MagicMock

import pytest

from hoplyra.chains import deployer as module
from hoplyra.chains.planner import build_plan


@pytest.mark.parametrize('servers', [('a', 'b', 'a'), ('a', 'b', 'a', 'c', 'a', 'd', 'a')])
def test_visit_resources_and_downstream(monkeypatch, servers):
    hops = [{'id': f'h{i}', 'serverId': sid,
             'protocol': 'xray' if sid == 'a' else 'tor'}
            for i, sid in enumerate(servers)]
    hosts = {sid: f'host-{sid}' for sid in servers}
    plan = build_plan(hops, hosts)
    runners = {}
    uploads = {}
    for sid, host in hosts.items():
        runner = MagicMock()
        runner.target.host = host
        runner.target.is_local = False
        runner.upload_text.side_effect = lambda path, content, *a, sid=sid, **kw: uploads.__setitem__((sid, path), content)
        runners[sid] = runner
    for name in ('check_deploy_cancel', 'recheck_runners_online', '_ensure_compose_images', 'podman_compose_up', 'mkdir_remote'):
        monkeypatch.setattr(module, name, lambda *a, **kw: None)
    touched = []
    result = module.ChainDeployer('/tmp/data').deploy(runners, plan, 'cfg-12345678', on_server_touched=touched.append)
    assert len(touched) == len(set(servers))
    assert '/hop-0/' in result.instance_path
    configs = {}
    names = []
    for (sid, path), content in uploads.items():
        if path.endswith('.json') and 'config' in path:
            index = int(path.split('/hop-')[1].split('/')[0])
            configs[index] = json.loads(content)
        if path.endswith('docker-compose.yml'):
            names.extend(line.strip() for line in content.splitlines() if 'container_name:' in line)
    assert len(names) == len(set(names))
    assert set(configs) == set(range(len(hops)))
    listeners = [configs[i]['inbounds'][0]['port'] for i, sid in enumerate(servers) if sid == 'a']
    assert len(listeners) == len(set(listeners))
    for i in range(len(hops) - 1):
        outbound = next(o for o in configs[i]['outbounds'] if o['protocol'] == 'socks' and o['tag'] != 'tor')
        target = outbound['settings']['servers'][0]
        assert target['address'] == hosts[servers[i + 1]]
        assert target['port'] == configs[i + 1]['inbounds'][0]['port']
    assert result.container_name in '\n'.join(names)


@pytest.mark.parametrize('protocols,servers', [
    (['xray', 'tor', 'tor'], ['a', 'b', 'a']),
    (['awg', 'tor', 'xray'], ['a', 'b', 'a']),
    (['xray', 'tor', 'xray', 'tor'], ['a', 'a', 'b', 'a']),
])
def test_conflicting_repeat_roles_rejected(protocols, servers):
    with pytest.raises(ValueError):
        build_plan([{'id': str(i), 'protocol': p, 'serverId': s}
                    for i, (p, s) in enumerate(zip(protocols, servers))],
                   {s: s for s in servers})


def test_repeat_failure_ownership_and_retry_context(monkeypatch):
    plan = build_plan([{'id': str(i), 'protocol': p, 'serverId': s}
                       for i, (p, s) in enumerate([('xray', 'a'), ('tor', 'b'), ('xray', 'a')])],
                      {'a': 'host-a', 'b': 'host-b'})
    monkeypatch.setattr(module, 'check_deploy_cancel', lambda *a: None)
    monkeypatch.setattr(module, 'recheck_runners_online', lambda *a: None)
    monkeypatch.setattr(module.ChainDeployer, '_deploy_exit_server', lambda *a: module.ExitEndpoint('host-a', 20002))
    def fail(*args):
        raise RuntimeError('relay failed')
    monkeypatch.setattr(module.ChainDeployer, '_deploy_relay_server', fail)
    worker = module.ChainDeployer('/tmp/data')
    touched = []
    with pytest.raises(RuntimeError, match='relay failed'):
        worker.deploy({'a': MagicMock(), 'b': MagicMock()}, plan, 'cfg-12345678', on_server_touched=touched.append)
    assert touched == ['a', 'b']
    assert worker._resource_tag('cfg-12345678') == '12345678'
    assert worker._listener_port(1080) == 1080
