import os
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import tempfile

import pytest
from werkzeug.security import check_password_hash

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + str(Path(_temp.name) / 'login-limits.db'))
os.environ.setdefault('SECRET_KEY', 'test-secret-key-with-more-than-thirty-two-characters')
os.environ.setdefault('INITIAL_ADMIN_PASSWORD', 'test-initial-password')

from app.db import Base, LoginLimit, Session, engine, init_db
from app import login_limits
from app.web import create_app


SECRET = 'test-secret-key-with-more-than-thirty-two-characters'


@pytest.fixture(autouse=True)
def reset_db(monkeypatch):
    monkeypatch.delenv('TRUSTED_PROXY_CIDRS', raising=False)
    Base.metadata.drop_all(engine)
    init_db()


def attempt(client, ip='192.0.2.1', forwarded=None, payload=None):
    token = client.get('/api/session').json['csrf']
    headers = {'X-CSRF-Token': token}
    if forwarded is not None:
        headers['X-Forwarded-For'] = forwarded
    if payload is None:
        payload = {'username': 'admin', 'password': 'incorrect-password'}
    return client.post('/api/login', json=payload, headers=headers, environ_overrides={'REMOTE_ADDR': ip})


def test_client_budget_blocks_before_hash_and_recovers_without_extending_wait(monkeypatch):
    timestamp = datetime(2026, 1, 1)
    monkeypatch.setattr(login_limits, 'now', lambda: timestamp)
    client = create_app().test_client()
    for _ in range(5):
        assert attempt(client).status_code == 401
    monkeypatch.setattr('app.web.check_password_hash', lambda *args: pytest.fail('Blocked request reached password hashing'))
    blocked = attempt(client, payload={'username': 'admin', 'password': 'test-initial-password'})
    assert blocked.status_code == 429
    assert blocked.headers['Retry-After'] == '60'
    assert blocked.json['retry_after'] == 60
    assert blocked.headers['Cache-Control'] == 'no-store'
    timestamp += timedelta(seconds=21)
    assert attempt(client).headers['Retry-After'] == '39'
    timestamp += timedelta(seconds=39)
    monkeypatch.setattr('app.web.check_password_hash', check_password_hash)
    assert attempt(client, payload={'username': 'admin', 'password': 'test-initial-password'}).status_code == 200


def test_new_sessions_apps_and_changed_usernames_do_not_reset_budget():
    for number in range(5):
        client = create_app().test_client()
        assert attempt(client, payload={'username': f'rotated-{number}', 'password': 'wrong'}).status_code == 401
    assert attempt(create_app().test_client()).status_code == 429
    assert attempt(create_app().test_client(), ip='192.0.2.2').status_code == 401


def test_successful_logins_count_but_signed_in_sessions_are_not_blocked():
    client = create_app().test_client()
    for _ in range(5):
        assert attempt(client, payload={'username': 'admin', 'password': 'test-initial-password'}).status_code == 200
    assert attempt(client).status_code == 429
    assert client.get('/api/settings/account').status_code == 200


def test_catalog_budget_limits_distributed_attempts_and_does_not_grow_when_blocked(monkeypatch):
    timestamp = datetime(2026, 1, 1)
    monkeypatch.setattr(login_limits, 'now', lambda: timestamp)
    for number in range(20):
        assert login_limits.reserve_login_attempt(f'192.0.2.{number}', SECRET) == 0
    for number in range(20, 60):
        assert login_limits.reserve_login_attempt(f'192.0.2.{number}', SECRET) == 60
    with Session() as db:
        assert db.query(LoginLimit).count() == 21
        assert db.get(LoginLimit, 'global').attempts == 20
    timestamp += timedelta(seconds=60)
    assert login_limits.reserve_login_attempt('192.0.2.100', SECRET) == 0


@pytest.mark.parametrize('same_client, expected', [(True, 5), (False, 20)])
def test_concurrent_reservations_cannot_exceed_budget(same_client, expected):
    def reserve(number):
        address = '192.0.2.1' if same_client else f'192.0.2.{number}'
        return login_limits.reserve_login_attempt(address, SECRET)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(reserve, range(32)))
    assert results.count(0) == expected
    with Session() as db:
        assert db.get(LoginLimit, 'global').attempts == expected


@pytest.mark.parametrize('same_client, expected', [(True, 5), (False, 20)])
def test_independent_processes_share_atomic_budgets(same_client, expected):
    code = """
import json, os, sys
os.environ['DATABASE_URL'] = sys.argv[1]
from app.login_limits import reserve_login_attempt
from app.db import engine
addresses = json.loads(sys.argv[2])
try:
    print(json.dumps([reserve_login_attempt(address, 'test-shared-secret') for address in addresses]))
finally:
    engine.dispose()
"""
    environment = os.environ | {'PYTHONPATH': os.pathsep.join(str(path) for path in sys.path if path)}
    def reserve_batch(number):
        addresses = ['192.0.2.1' if same_client else f'192.0.2.{number * 10 + offset}' for offset in range(10)]
        output = subprocess.check_output([sys.executable, '-c', code, str(engine.url), json.dumps(addresses)],
                                         env=environment, text=True, timeout=30)
        return json.loads(output)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [result for batch in pool.map(reserve_batch, range(4)) for result in batch]
    assert results.count(0) == expected
    with Session() as db:
        assert db.get(LoginLimit, 'global').attempts == expected


def test_restart_preserves_budgets_and_clients_are_not_stored_in_plaintext():
    for _ in range(5):
        assert login_limits.reserve_login_attempt('192.0.2.12', SECRET) == 0
    init_db()
    assert login_limits.reserve_login_attempt('192.0.2.12', SECRET) > 0
    with Session() as db:
        assert all('192.0.2.12' not in row.key for row in db.query(LoginLimit))


def test_stale_rows_are_cleaned_without_resetting_active_client_budgets(monkeypatch):
    timestamp = datetime(2026, 1, 1, 12)
    monkeypatch.setattr(login_limits, 'now', lambda: timestamp)
    with Session.begin() as db:
        db.add(LoginLimit(key='global', window_start=timestamp - timedelta(minutes=2), attempts=20))
        db.add(LoginLimit(key='stale', window_start=timestamp - timedelta(hours=2), attempts=5))
        db.add(LoginLimit(key='recent', window_start=timestamp - timedelta(minutes=2), attempts=5))
    assert login_limits.reserve_login_attempt('192.0.2.1', SECRET) == 0
    with Session() as db:
        assert db.get(LoginLimit, 'stale') is None
        assert db.get(LoginLimit, 'recent').attempts == 5


def test_missing_csrf_does_not_consume_login_budget():
    client = create_app().test_client()
    assert client.post('/api/login', json={}).status_code == 403
    with Session() as db:
        assert db.query(LoginLimit).count() == 0


def test_invalid_payloads_also_consume_budget():
    client = create_app().test_client()
    for _ in range(5):
        assert attempt(client, payload=[]).status_code == 400
    assert attempt(client).status_code == 429


def test_forwarded_header_spoofing_cannot_change_direct_client_budget():
    client = create_app().test_client()
    for number in range(5):
        assert attempt(client, forwarded=f'198.51.100.{number}').status_code == 401
    assert attempt(client, forwarded='198.51.100.100').status_code == 429


def test_trusted_proxy_has_separate_client_budgets_and_ignores_forged_prefix(monkeypatch):
    monkeypatch.setenv('TRUSTED_PROXY_CIDRS', '10.0.0.10/32')
    client = create_app().test_client()
    for number in range(5):
        assert attempt(client, ip='10.0.0.10', forwarded=f'198.51.100.{number}, 192.0.2.1').status_code == 401
    assert attempt(client, ip='10.0.0.10', forwarded='198.51.100.99, 192.0.2.1').status_code == 429
    assert attempt(client, ip='10.0.0.10', forwarded='192.0.2.2').status_code == 401


@pytest.mark.parametrize('peer, forwarded, networks, expected', [
    ('192.0.2.1', '198.51.100.1', '', '192.0.2.1'),
    ('192.0.2.1', '198.51.100.1', '10.0.0.0/24', '192.0.2.1'),
    ('10.0.0.10', '198.51.100.1, 10.0.0.20', '10.0.0.0/24', '198.51.100.1'),
    ('10.0.0.10', 'forged, 192.0.2.1', '10.0.0.10', '10.0.0.10'),
    ('10.0.0.10', ', 192.0.2.1', '10.0.0.10', '10.0.0.10'),
    ('10.0.0.10', ','.join(['192.0.2.1'] * 17), '10.0.0.10', '10.0.0.10'),
    ('::ffff:192.0.2.1', None, '', '192.0.2.1'),
    ('2001:db8::10', '2001:db8:1::1', '2001:db8::10/128', '2001:db8:1::1'),
    (None, '192.0.2.1', '10.0.0.10', 'unknown'),
])
def test_proxy_chain_validation(peer, forwarded, networks, expected):
    trusted = login_limits.trusted_proxy_networks(networks)
    assert login_limits.client_address(peer, forwarded, trusted) == expected


@pytest.mark.parametrize('value', ['not-a-network', '0.0.0.0/0', '::/0'])
def test_unsafe_proxy_configuration_fails_startup(monkeypatch, value):
    monkeypatch.setenv('TRUSTED_PROXY_CIDRS', value)
    with pytest.raises(RuntimeError, match='TRUSTED_PROXY_CIDRS'):
        create_app()
