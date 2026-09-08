import os
import tempfile
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + str(Path(_temp.name) / 'preview-policy.db'))
os.environ.setdefault('SECRET_KEY', 'test-secret-key-with-more-than-thirty-two-characters')
os.environ.setdefault('CATALOG_PASSWORD', 'test-password-long')

from app.db import Base, engine, Session, Photo, Scan, Setting, init_db
from app import worker, imaging
import app.web as web
from app.imaging import cache_file
from app.web import create_app


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(engine)
    init_db()


@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()
    token = client.get('/api/session').json['csrf']
    response = client.post('/api/login', json={'password': 'test-password-long'},
                           headers={'X-CSRF-Token': token})
    client.csrf = response.json['csrf']
    return client


def test_preview_quality_setting(client, tmp_path, monkeypatch):
    cache = tmp_path / 'cache'
    external = tmp_path / 'storage'
    external.mkdir()
    monkeypatch.setenv('CACHE_DIR', str(cache))
    monkeypatch.setenv('EXTERNAL_STORAGE_ROOT', str(external))
    headers = {'X-CSRF-Token': client.csrf}

    initial = client.get('/api/settings/previews').json
    assert initial['preview_quality'] == 88
    assert 95 in initial['preview_quality_options']

    changed = client.put('/api/settings/previews', json={
        'folder': str(cache / 'previews'), 'preview_edge': 1920, 'preview_quality': 95
    }, headers=headers)
    assert changed.status_code == 200
    assert changed.json['preview_edge'] == 1920
    assert changed.json['preview_quality'] == 95

    invalid = client.put('/api/settings/previews', json={
        'folder': str(cache / 'previews'), 'preview_edge': 1920, 'preview_quality': 99
    }, headers=headers)
    assert invalid.status_code == 400


def test_scan_queue_records_skip_previews(client):
    headers = {'X-CSRF-Token': client.csrf}
    queued = client.post('/api/scan', json={'skip_previews': True}, headers=headers)
    assert queued.status_code == 202
    job_id = queued.json['scan']['id']
    with Session() as db:
        setting = db.get(Setting, f'scan_skip_previews:{job_id}')
        assert setting is not None and setting.value == '1'


def test_worker_thumbnail_only_scan(tmp_path, monkeypatch):
    photos = tmp_path / 'photos'
    photos.mkdir()
    source = photos / 'one.cr3'
    source.write_bytes(b'raw')
    cache = tmp_path / 'cache'
    external = tmp_path / 'storage'
    external.mkdir()
    monkeypatch.setenv('CACHE_DIR', str(cache))
    monkeypatch.setenv('EXTERNAL_STORAGE_ROOT', str(external))
    monkeypatch.setattr(worker, 'ROOT', photos)
    monkeypatch.setattr(worker, 'CACHE', str(cache))

    monkeypatch.setattr(worker, 'metadata_batch', lambda paths: {
        str(path): {'Make': 'Canon', 'Model': 'EOS R6', 'LensModel': 'RF50mm F1.8 STM'}
        for path in paths
    })
    preview_calls = []

    def fake_thumbnail(path, metadata, root, key):
        output = cache_file(root, key, 'thumb')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'thumb')

    def fake_previews(*args, **kwargs):
        preview_calls.append((args, kwargs))

    monkeypatch.setattr(worker, 'make_thumbnail', fake_thumbnail)
    monkeypatch.setattr(worker, 'make_previews', fake_previews)

    with Session.begin() as db:
        job = Scan()
        db.add(job)
        db.flush()
        job_id = job.id
        db.add(Setting(key=f'scan_skip_previews:{job_id}', value='1'))

    worker.scan(job_id)

    with Session() as db:
        job = db.get(Scan, job_id)
        photo = db.query(Photo).one()
        assert job.state == 'done' and job.indexed == 1
        assert photo.cache_key is not None
        assert photo.preview_error is None
        key = photo.cache_key

    assert preview_calls == []
    assert cache_file(cache / 'thumbnails', key, 'thumb').is_file()
    assert not cache_file(cache / 'previews', key, 'preview').exists()


def test_missing_preview_generated_once_on_open(client, tmp_path, monkeypatch):
    photos = tmp_path / 'photos'
    photos.mkdir()
    source = photos / 'one.cr3'
    source.write_bytes(b'raw')
    cache = tmp_path / 'cache'
    external = tmp_path / 'storage'
    external.mkdir()
    monkeypatch.setenv('PHOTO_ROOT', str(photos))
    monkeypatch.setenv('CACHE_DIR', str(cache))
    monkeypatch.setenv('EXTERNAL_STORAGE_ROOT', str(external))

    key = 'd' * 64
    with Session.begin() as db:
        db.add(Setting(key='preview_folder', value=str(cache / 'previews')))
        db.add(Setting(key='preview_edge', value='1920'))
        db.add(Setting(key='preview_quality', value='95'))
        photo = Photo(path_hash='on-demand', path=str(source), filename=source.name,
                      size=3, mtime_ns=1, cache_key=key,
                      metadata_json={'Orientation': 1}, preview_error='old error')
        db.add(photo)
        db.flush()
        photo_id = photo.id

    calls = []

    def fake_preview(path, metadata, root, cache_key, edge=None, quality=88):
        calls.append((path, root, cache_key, edge, quality))
        output = cache_file(root, cache_key, 'preview')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'jpeg-preview')

    monkeypatch.setattr(web, 'make_preview', fake_preview)

    first = client.get(f'/media/{photo_id}/preview')
    second = client.get(f'/media/{photo_id}/preview')
    assert first.status_code == 200 and first.data == b'jpeg-preview'
    assert second.status_code == 200 and second.data == b'jpeg-preview'
    assert len(calls) == 1
    assert calls[0][3:] == (1920, 95)

    with Session() as db:
        assert db.get(Photo, photo_id).preview_error is None


def test_parallelism_setting_validation(client):
    headers = {'X-CSRF-Token': client.csrf}
    queued = client.post('/api/scan', json={'parallelism': 4}, headers=headers)
    assert queued.status_code == 202
    job_id = queued.json['scan']['id']
    with Session() as db:
        assert db.get(Setting, f'scan_parallelism:{job_id}').value == '4'


def test_parallel_scan_executes_generated_media_concurrently(tmp_path, monkeypatch):
    photos = tmp_path / 'photos'
    photos.mkdir()
    for index in range(8):
        (photos / f'{index}.cr3').write_bytes(b'raw-' + bytes([index]))
    cache = tmp_path / 'cache'
    external = tmp_path / 'storage'
    external.mkdir()
    monkeypatch.setenv('CACHE_DIR', str(cache))
    monkeypatch.setenv('EXTERNAL_STORAGE_ROOT', str(external))
    monkeypatch.setattr(worker, 'ROOT', photos)
    monkeypatch.setattr(worker, 'CACHE', str(cache))
    monkeypatch.setattr(worker, 'metadata_batch', lambda paths: {
        str(path): {'Make': 'Canon', 'Model': 'EOS R6', 'LensModel': 'Test lens'} for path in paths
    })

    lock = threading.Lock()
    active = 0
    peak = 0

    def fake_thumbnail(path, metadata, root, key):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.04)
            output = cache_file(root, key, 'thumb')
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b'thumb')
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(worker, 'make_thumbnail', fake_thumbnail)
    with Session.begin() as db:
        job = Scan()
        db.add(job)
        db.flush()
        job_id = job.id
        db.add(Setting(key=f'scan_skip_previews:{job_id}', value='1'))
        db.add(Setting(key=f'scan_parallelism:{job_id}', value='4'))

    worker.scan(job_id)
    with Session() as db:
        job = db.get(Scan, job_id)
        assert job.state == 'done'
        assert job.indexed == 8
        assert job.errors == 0
        assert db.query(Photo).count() == 8
    assert peak >= 2


def test_thumbnail_jpeg_skips_optimization(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(imaging, 'open_preview', lambda path, metadata: Image.new('RGB', (32, 32)))

    def fake_save(image, output, edge, quality, optimize=True):
        calls.append((edge, quality, optimize))

    monkeypatch.setattr(imaging, '_save_scaled', fake_save)
    imaging.make_thumbnail(tmp_path / 'one.cr3', {}, tmp_path / 'thumbs', 'a' * 64)
    assert calls == [(480, 80, False)]


def test_combined_preview_keeps_preview_optimized_but_thumbnail_fast(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(imaging, 'open_preview', lambda path, metadata: Image.new('RGB', (64, 64)))

    def fake_save(image, output, edge, quality, optimize=True):
        calls.append((edge, quality, optimize))

    monkeypatch.setattr(imaging, '_save_scaled', fake_save)
    imaging.make_previews(tmp_path / 'one.cr3', {}, tmp_path / 'previews', 'b' * 64,
                          tmp_path / 'thumbs', 1920, 88)
    assert calls == [(1920, 88, True), (480, 80, False)]


def test_fast_smb_setting_is_recorded(client):
    headers = {'X-CSRF-Token': client.csrf}
    queued = client.post('/api/scan', json={'skip_post_stat': True, 'parallelism': 4}, headers=headers)
    assert queued.status_code == 202
    job_id = queued.json['scan']['id']
    with Session() as db:
        setting = db.get(Setting, f'scan_skip_post_stat:{job_id}')
        assert setting is not None and setting.value == '1'



def test_import_options_are_persistent(client):
    headers = {'X-CSRF-Token': client.csrf}
    initial = client.get('/api/settings/import')
    assert initial.status_code == 200
    assert initial.json['parallelism'] == 4
    saved = client.put('/api/settings/import', json={
        'parallelism': 6, 'skip_previews': True, 'skip_imported': True,
        'force': False, 'skip_post_stat': True,
    }, headers=headers)
    assert saved.status_code == 200
    assert saved.json['parallelism'] == 6 and saved.json['skip_post_stat'] is True
    again = client.get('/api/settings/import').json
    assert again == saved.json
    queued = client.post('/api/scan', json={}, headers=headers)
    assert queued.status_code == 202
    job_id = queued.json['scan']['id']
    with Session() as db:
        assert db.get(Setting, f'scan_parallelism:{job_id}').value == '6'
        assert db.get(Setting, f'scan_skip_previews:{job_id}').value == '1'
        assert db.get(Setting, f'scan_skip_imported:{job_id}').value == '1'
        assert db.get(Setting, f'scan_skip_post_stat:{job_id}').value == '1'


def test_favorite_toggle_and_filter(client):
    headers = {'X-CSRF-Token': client.csrf}
    with Session.begin() as db:
        first = Photo(path_hash='favorite-a', path='/photos/a.cr3', filename='a.cr3', size=1, mtime_ns=1,
                      camera='Canon', lens='Lens A', metadata_json={})
        second = Photo(path_hash='favorite-b', path='/photos/b.cr3', filename='b.cr3', size=1, mtime_ns=1,
                       camera='Canon', lens='Lens B', metadata_json={})
        db.add_all([first, second]); db.flush(); first_id = first.id
    changed = client.put(f'/api/photos/{first_id}/favorite', json={'favorite': True}, headers=headers)
    assert changed.status_code == 200 and changed.json['favorite'] is True
    filtered = client.get('/api/photos?favorite=1')
    assert filtered.status_code == 200
    assert filtered.json['total'] == 1
    assert filtered.json['items'][0]['id'] == first_id
    assert filtered.json['items'][0]['favorite'] is True
    with Session() as db:
        assert db.get(Photo, first_id).favorite is True


def test_saved_edits_gallery_download_and_delete(client, tmp_path, monkeypatch):
    cache = tmp_path / 'cache'; storage = tmp_path / 'storage'; storage.mkdir()
    edit_root = storage / 'edits'; edit_root.mkdir()
    monkeypatch.setenv('CACHE_DIR', str(cache))
    monkeypatch.setenv('EXTERNAL_STORAGE_ROOT', str(storage))
    with Session.begin() as db:
        db.add(Setting(key='edit_folder', value=str(edit_root)))
    saved = edit_root / 'sample edit.jpg'
    saved.write_bytes(b'jpeg-data')
    listing = client.get('/api/edits')
    assert listing.status_code == 200 and listing.json['total'] == 1
    item = listing.json['items'][0]
    assert item['filename'] == saved.name
    downloaded = client.get(item['download_url'])
    assert downloaded.status_code == 200 and downloaded.data == b'jpeg-data'
    deleted = client.delete('/api/edits/sample%20edit.jpg', headers={'X-CSRF-Token': client.csrf})
    assert deleted.status_code == 200
    assert not saved.exists()
