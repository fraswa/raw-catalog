import os
from pathlib import Path
import tempfile

import pytest
from werkzeug.security import check_password_hash

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + str(Path(_temp.name) / 'accounts.db'))
os.environ.setdefault('SECRET_KEY', 'test-secret-key-with-more-than-thirty-two-characters')
os.environ.setdefault('INITIAL_ADMIN_PASSWORD', 'test-initial-password')

from app.db import Account, Base, engine, init_db, Session, Photo, Scan, Setting
from app.web import create_app


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(engine)
    init_db()


def login(client, username='admin', password='test-initial-password'):
    token = client.get('/api/session').json['csrf']
    response = client.post('/api/login', json={'username': username, 'password': password},
                           headers={'X-CSRF-Token': token})
    if response.status_code == 200:
        client.csrf = response.json['csrf']
    return response


@pytest.fixture
def client():
    client = create_app().test_client()
    assert login(client).status_code == 200
    return client


def update(client, **changes):
    payload = {'username': 'admin', 'current_password': 'test-initial-password'} | changes
    return client.put('/api/settings/account', json=payload, headers={'X-CSRF-Token': client.csrf})


def test_initial_account_login_hash_and_no_environment_password(monkeypatch):
    monkeypatch.delenv('CATALOG_PASSWORD', raising=False)
    client = create_app().test_client()
    initial = client.get('/api/session').json
    assert initial['authenticated'] is False and initial['username'] is None
    response = login(client)
    assert response.status_code == 200 and response.json['csrf'] != initial['csrf']
    assert client.get('/api/session').json['username'] == 'admin'
    with Session() as db:
        account = db.get(Account, 1)
        assert account.password_hash != 'test-initial-password'
        assert check_password_hash(account.password_hash, 'test-initial-password')
    assert client.get('/api/settings/account').json == {'username': 'admin'}


@pytest.mark.parametrize('payload', [None, [], {'password': 'test-initial-password'}, {'username': 'wrong', 'password': 'test-initial-password'},
                                     {'username': 'admin', 'password': 'wrong'}, {'username': [], 'password': 'test-initial-password'},
                                     {'username': 'admin', 'password': 123}, {'username': 'admin', 'password': 'x' * 1025}])
def test_login_rejects_incorrect_and_invalid_credentials(payload):
    client = create_app().test_client()
    token = client.get('/api/session').json['csrf']
    response = client.post('/api/login', json=payload, headers={'X-CSRF-Token': token})
    assert response.status_code in (400, 401)
    assert client.get('/api/session').json['authenticated'] is False


def test_password_only_cookie_cannot_access_catalog():
    client = create_app().test_client()
    with client.session_transaction() as cookie:
        cookie.update(authenticated=True, csrf='old-cookie-token')
    assert client.get('/api/settings/account').status_code == 401
    assert client.get('/api/session').json['authenticated'] is False


def test_account_requires_authentication_csrf_and_current_password(client):
    anonymous = create_app().test_client()
    assert anonymous.get('/api/settings/account').status_code == 401
    assert anonymous.put('/api/settings/account', json={}).status_code == 401
    assert client.put('/api/settings/account', json={}).status_code == 403
    assert update(client, current_password='wrong', username='changed').status_code == 400
    assert client.get('/api/settings/account').json['username'] == 'admin'


def test_change_both_credentials_revokes_other_sessions_and_persists(client):
    other = create_app().test_client()
    assert login(other).status_code == 200
    new_password = 'new-catalog-password'
    response = update(client, username='photographer', new_password=new_password, confirm_password=new_password)
    assert response.status_code == 200
    assert response.json['csrf'] == client.csrf
    assert client.get('/api/session').json['username'] == 'photographer'
    assert other.get('/api/settings/account').status_code == 401
    assert other.get('/api/session').json['authenticated'] is False
    fresh = create_app().test_client()
    assert login(fresh).status_code == 401
    assert login(fresh, 'photographer', new_password).status_code == 200
    with Session() as db:
        account = db.get(Account, 1)
        saved_hash, saved_version = account.password_hash, account.session_version
        assert check_password_hash(saved_hash, new_password)
    init_db()
    with Session() as db:
        account = db.get(Account, 1)
        assert account.username == 'photographer'
        assert (account.password_hash, account.session_version) == (saved_hash, saved_version)


def test_username_only_change_keeps_password_and_does_not_interrupt_scan(client):
    with Session.begin() as db:
        job = Scan(state='running', message='Scanning')
        db.add(job); db.flush(); job_id = job.id
        saved_hash = db.get(Account, 1).password_hash
    assert update(client, username='cosplay').status_code == 200
    with Session() as db:
        assert db.get(Account, 1).password_hash == saved_hash
        job = db.get(Scan, job_id)
        assert job.state == 'running' and job.cancel is False
    fresh = create_app().test_client()
    assert login(fresh, 'cosplay').status_code == 200


def test_password_only_change_and_logout(client):
    password = 'another-password'
    assert update(client, new_password=password, confirm_password=password).status_code == 200
    assert client.post('/api/logout', headers={'X-CSRF-Token': client.csrf}).status_code == 200
    assert client.get('/api/session').json['authenticated'] is False
    assert login(client, password=password).status_code == 200


@pytest.mark.parametrize('changes', [
    {'username': ''}, {'username': 'x' * 65}, {'username': 'bad name'}, {'username': '<script>'}, {'username': []},
    {'current_password': None}, {'new_password': None}, {'new_password': 'short', 'confirm_password': 'short'},
    {'new_password': 'x' * 1025}, {'new_password': 'long-new-password', 'confirm_password': 'different'},
    {'new_password': 'long-new-password'},
    {'confirm_password': 'unexpected'}, {'confirm_password': []},
])
def test_invalid_account_changes_are_atomic(client, changes):
    with Session() as db:
        account = db.get(Account, 1)
        previous = (account.username, account.password_hash, account.session_version)
    assert update(client, **changes).status_code == 400
    with Session() as db:
        account = db.get(Account, 1)
        assert (account.username, account.password_hash, account.session_version) == previous


def test_upgrade_bootstraps_once_preserves_catalog_and_ignores_legacy_password(monkeypatch):
    with Session.begin() as db:
        db.add(Photo(path_hash='legacy', path='/photos/legacy.cr3', filename='legacy.cr3', size=42, mtime_ns=1))
        db.add(Setting(key='edit_folder', value='/storage/edits'))
        db.add(Scan(state='done', indexed=10))
    Account.__table__.drop(engine)
    monkeypatch.setenv('CATALOG_PASSWORD', 'legacy-password')
    init_db()
    client = create_app().test_client()
    assert login(client, password='legacy-password').status_code == 401
    assert login(client).status_code == 200
    with Session() as db:
        assert db.query(Photo).count() == 1
        assert db.get(Setting, 'edit_folder').value == '/storage/edits'
        assert db.query(Scan).one().indexed == 10
        assert db.query(Account).count() == 1


def test_unchanged_account_keeps_other_sessions(client):
    other = create_app().test_client()
    assert login(other).status_code == 200
    assert update(client).status_code == 200
    assert other.get('/api/settings/account').status_code == 200


@pytest.mark.parametrize('password', [None, '', 'too-short', 'x' * 1025])
def test_first_start_requires_a_valid_chosen_password(monkeypatch, password):
    Account.__table__.drop(engine)
    if password is None:
        monkeypatch.delenv('INITIAL_ADMIN_PASSWORD', raising=False)
    else:
        monkeypatch.setenv('INITIAL_ADMIN_PASSWORD', password)
    with pytest.raises(RuntimeError, match='INITIAL_ADMIN_PASSWORD'):
        init_db()
    with Session() as db:
        assert db.query(Account).count() == 0


@pytest.mark.parametrize('password', [None, 'too-short', 'different-initial-password'])
def test_existing_account_ignores_initial_password(monkeypatch, client, password):
    with Session() as db:
        account = db.get(Account, 1)
        previous = (account.username, account.password_hash, account.session_version)
    if password is None:
        monkeypatch.delenv('INITIAL_ADMIN_PASSWORD', raising=False)
    else:
        monkeypatch.setenv('INITIAL_ADMIN_PASSWORD', password)
    init_db()
    with Session() as db:
        account = db.get(Account, 1)
        assert (account.username, account.password_hash, account.session_version) == previous
    assert client.get('/api/settings/account').status_code == 200
    assert login(create_app().test_client()).status_code == 200
