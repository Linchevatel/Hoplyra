from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hoplyra import db, deploy_log
from hoplyra.remote import describe_command


@pytest.fixture(autouse=True)
def clean_registry():
    with deploy_log._condition:
        deploy_log._logs.clear()
    yield
    with deploy_log._condition:
        deploy_log._logs.clear()


def test_ring_since_and_snapshot_copy():
    for i in range(2010):
        deploy_log.publish('ring', f'Line {i}')
    events = deploy_log.snapshot('ring')
    assert len(events) == 2000
    assert events[0]['seq'] == 11
    assert [event['seq'] for event in deploy_log.snapshot('ring', 2008)] == [2009, 2010]
    events[-1]['message'] = 'mutated'
    assert deploy_log.snapshot('ring')[-1]['message'] == 'Line 2009'


@pytest.mark.parametrize('prefix', [
    'PrivateKey = ', 'PresharedKey = ', 'password=', 'password: ', 'password ',
    'pass=', '--pass ', '--pass=', '--password ', 'auth: ', 'PSK=',
    '"password": "', 'headerProtectionKey=', 'auth_secret=',
    'SSHPASS=', 'token=', 'secret=',
])
def test_secret_assignments(prefix):
    secret = 'small-secret with spaces; other words'
    deploy_log.publish('redact', prefix + secret, stage='password=stage-secret')
    text = json.dumps(deploy_log.snapshot('redact'))
    assert 'small-secret' not in text
    assert 'other words' not in text
    assert 'stage-secret' not in text
    assert '<redacted>' in text


def test_uri_credentials_and_base64url_and_yaml_redaction():
    uris = [
        'socks5://user:pass123@1.2.3.4:1080',
        'hysteria2://primary%3Apass456@1.2.3.4:443/?insecure=1',
        'vless://uuid-secret-here@1.2.3.4:443?encryption=none',
    ]
    for uri in uris:
        deploy_log.publish('uri', f'Connecting to {uri}')
        msg = deploy_log.snapshot('uri')[-1]['message']
        assert 'pass123' not in msg
        assert 'pass456' not in msg
        assert 'uuid-secret-here' not in msg
        assert '<redacted>@1.2.3.4' in msg

    # Base64url with - and _ (x25519 reality private key)
    b64url_key = 'abcdef1234567890_-abcdef1234567890_-abcd12='
    deploy_log.publish('b64url', f'Key is {b64url_key}')
    assert b64url_key not in deploy_log.snapshot('b64url')[-1]['message']
    assert '<redacted>' in deploy_log.snapshot('b64url')[-1]['message']

    # Multiline YAML auth
    yaml_snippet = "auth:\n  userpass:\n    primary: \"super_secret_password\""
    deploy_log.publish('yaml', yaml_snippet)
    assert 'super_secret_password' not in deploy_log.snapshot('yaml')[-1]['message']
    assert '<redacted>' in deploy_log.snapshot('yaml')[-1]['message']


def test_private_keys_psk_pem_truncation_and_public_pin():
    key = 'AbCd1234+/' * 5 + '=='
    pem = '-----BEGIN OPENSSH PRIVATE KEY-----\nshort secret\n-----END OPENSSH PRIVATE KEY-----'
    pin = 'Z' * 43 + '='
    deploy_log.publish('secrets', f'{pem}\n{key}\npinSHA256={pin}')
    text = deploy_log.snapshot('secrets')[0]['message']
    assert key not in text and 'short secret' not in text
    assert f'pinSHA256={pin}' in text
    deploy_log.publish('secrets', key * 50)
    assert len(deploy_log.snapshot('secrets')[-1]['message']) <= 500
    assert key[:20] not in deploy_log.snapshot('secrets')[-1]['message']


def test_wireguard_keys_and_command_secrets():
    raw_key = 'W1r3Gu4rdPr1v4t3K3yS3cr3tT0k3nEx4mpl3K3y44C='
    raw_psk = 'Pr3Sh4r3dK3yS3cr3tF0rW1r3Gu4rdT0k3nEx4mpl3=='
    deploy_log.publish('wg', f'Setting up: PrivateKey = {raw_key} and PresharedKey = {raw_psk}')
    msg = deploy_log.snapshot('wg')[0]['message']
    assert raw_key not in msg
    assert raw_psk not in msg
    assert '<redacted>' in msg


def test_status_clear_prune_keeps_active():
    assert deploy_log.status('missing') == {'active': False, 'finished': False, 'ok': None, 'seq': 0}
    deploy_log.mark_active('active')
    for i in range(4):
        deploy_log.publish(str(i), 'done')
        deploy_log.mark_finished(str(i), bool(i % 2))
    deploy_log.prune(3)
    assert deploy_log.status('0')['seq'] == 0
    assert deploy_log.status('1')['seq'] == 0
    assert deploy_log.status('3') == {'active': False, 'finished': True, 'ok': True, 'seq': 1}
    assert deploy_log.status('active')['active']
    deploy_log.clear('3')
    assert deploy_log.snapshot('3') == []


def test_threads_wakeup_and_context_isolation():
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(deploy_log.wait_for_events, 'one', 0, 2)
        with deploy_log.deploy_context('one'):
            deploy_log.publish_current('hello')
        assert future.result(timeout=3)[0]['message'] == 'hello'
    deploy_log.publish_current('outside context')
    assert len(deploy_log.snapshot('one')) == 1
    def write():
        for _ in range(100):
            deploy_log.publish('thread', 'progress')
    threads = [threading.Thread(target=write) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [event['seq'] for event in deploy_log.snapshot('thread')] == list(range(1, 401))
    deploy_log.mark_finished('one', False)
    started = time.monotonic()
    assert deploy_log.wait_for_events('one', 1, 2) == []
    assert time.monotonic() - started < 0.1


@pytest.mark.parametrize(('cmd', 'expected'), [
    ('docker build --build-arg password=secret .', 'docker build'),
    ('podman-compose up --password secret', 'podman-compose up'),
    ('echo secret', None), ('bash -lc "docker build secret"', None),
    ('supersecret subcommand', None), ('docker "', None),
])
def test_describe_command_never_includes_arguments(cmd, expected):
    assert describe_command(cmd) == expected


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'hoplyra.db')
    db.init_db()
    from hoplyra.main import app
    monkeypatch.setattr('hoplyra.auth.is_authenticated', lambda req: True)
    with db.connect() as conn:
        conn.execute("INSERT INTO servers (id,name,host,port,username,created_at) VALUES ('s','test','192.0.2.1',22,'root','now')")
        conn.execute("INSERT INTO configs (id,server_id,protocol,status,created_at) VALUES ('cfg','s','wg','deploying','now')")
    return TestClient(app)


def test_endpoints_snapshot_stream_404_and_session_auth(client, monkeypatch):
    deploy_log.mark_active('cfg')
    deploy_log.publish('cfg', 'Ready', level='success')
    deploy_log.mark_finished('cfg', True)
    result = client.get('/api/configs/cfg/deploy-log').json()
    assert result['events'][0]['message'] == 'Ready'
    assert result['status']['finished']
    assert client.get('/api/configs/cfg/deploy-log?since=1').json()['events'] == []
    with client.stream('GET', '/api/configs/cfg/deploy-log/stream') as response:
        assert response.headers['content-type'].startswith('text/event-stream')
        text = ''.join(response.iter_text())
        assert 'Ready' in text and 'event: end' in text
    assert client.get('/api/configs/missing/deploy-log').status_code == 404
    assert client.get('/api/configs/missing/deploy-log/stream').status_code == 404
    monkeypatch.setattr('hoplyra.auth.is_authenticated', lambda req: False)
    assert client.get('/api/configs/cfg/deploy-log').status_code == 401
    assert client.get('/api/configs/cfg/deploy-log/stream').status_code == 401


def test_deploy_log_accessible_after_db_deletion(client):
    # Simulate config in deploy_log that was removed from database
    deploy_log.mark_active('deleted_cfg')
    deploy_log.publish('deleted_cfg', 'Cancelled', level='warn', stage='cancelled')
    deploy_log.mark_finished('deleted_cfg', False)

    # It does NOT exist in SQLite configs table
    res = client.get('/api/configs/deleted_cfg/deploy-log')
    assert res.status_code == 200
    data = res.json()
    assert data['events'][0]['message'] == 'Cancelled'
    assert data['status']['finished'] is True


def test_sse_wait_runs_off_event_loop(client):
    from hoplyra.main import stream_deploy_log
    from unittest.mock import AsyncMock
    async def run():
        loop_thread = threading.get_ident()
        def wait(config_id, since, timeout):
            assert threading.get_ident() != loop_thread
            deploy_log.publish(config_id, 'done')
            deploy_log.mark_finished(config_id, True)
            return deploy_log.snapshot(config_id, since)
        request = AsyncMock()
        request.is_disconnected.return_value = False
        with patch.object(deploy_log, 'wait_for_events', side_effect=wait):
            response = await stream_deploy_log('cfg', request)
            frames = [frame async for frame in response.body_iterator]
        assert any('event: end' in frame for frame in frames)
    asyncio.run(run())


def test_redact_redos_yaml_auth_no_secret():
    # 200 lines of auth without secret should complete virtually instantly (< 50ms)
    large_yaml = "auth:\n" + "  userpass:\n" + ("    comment: regular configuration line\n" * 200)
    start = time.monotonic()
    deploy_log.publish('redos', large_yaml)
    duration = time.monotonic() - start
    assert duration < 0.2
    assert "regular configuration line" in deploy_log.snapshot('redos')[0]['message']


def test_redact_uri_with_at_symbol_in_password():
    uri = "hysteria2://primary:my@pass@1.2.3.4:443/?insecure=1"
    deploy_log.publish('uri_at', f"Connecting to {uri}")
    msg = deploy_log.snapshot('uri_at')[0]['message']
    assert "my@pass" not in msg
    assert "primary" not in msg
    assert "hysteria2://<redacted>@1.2.3.4:443/?insecure=1" in msg


def test_redact_preserves_paths_with_uuid_and_english_phrases():
    path_msg = "awg-quick up /opt/hoplyra/instances/6825dad7-1234-5678-90ab-cdef01234567/awg_6825dad7.conf"
    deploy_log.publish('path', path_msg)
    assert deploy_log.snapshot('path')[0]['message'] == path_msg

    stop_msg = "Stopping compose stack in /opt/hoplyra/instances/6825dad7-1234-5678-90ab-cdef01234567"
    deploy_log.publish('stop', stop_msg)
    assert deploy_log.snapshot('stop')[0]['message'] == stop_msg

    phrase1 = "Generating secret key for OpenVPN"
    deploy_log.publish('eng1', phrase1)
    assert deploy_log.snapshot('eng1')[0]['message'] == phrase1

    phrase2 = "Refreshing auth token for registry"
    deploy_log.publish('eng2', phrase2)
    assert deploy_log.snapshot('eng2')[0]['message'] == phrase2


def test_format_command_for_log_compound_and_instances():
    from hoplyra.remote import format_command_for_log

    compound = "(podman build -t img .) || (docker build -t img .)"
    assert format_command_for_log(compound) == "(podman build -t img .) || (docker build -t img .)"

    single_parentheses = "(podman-compose up -d)"
    assert format_command_for_log(single_parentheses) == "podman-compose up -d"

    instance_cmd = "awg-quick up /opt/hoplyra/instances/6825dad7-1234-5678-90ab-cdef01234567/awg_6825dad7.conf"
    assert format_command_for_log(instance_cmd) == "awg-quick up awg_6825dad7.conf"

