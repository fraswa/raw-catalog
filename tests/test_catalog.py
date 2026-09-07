import hashlib
import os
import tempfile
from pathlib import Path

import pytest
from PIL import Image

# SQLite is only the isolated test backend; compose uses MariaDB.
_temp = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(_temp.name) / 'test.db')
os.environ['SECRET_KEY'] = 'test-secret-key-with-more-than-thirty-two-characters'
os.environ['CATALOG_PASSWORD'] = 'test-password-long'
os.environ['CACHE_DIR'] = str(Path(_temp.name) / 'cache')
from app.db import Base, engine, Session, Photo, Scan, Setting, init_db
from app.web import create_app
from app import worker
from app.imaging import orient, make_previews, cache_file
from app.storage import resolve_thumbnail_root


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
    response = client.post('/api/login', json={'password': 'test-password-long'}, headers={'X-CSRF-Token':token})
    client.csrf = response.json['csrf']
    return client


def seed():
    with Session.begin() as db:
        for i, camera, lens in [(1,'Canon EOS R6','EF 50mm f/1.2L'), (2,'Canon EOS R6','EF 24-70mm f/2.8L II'), (3,'Leica M Monochrom','Summilux 35mm'),(4,'Canon EOS R6','EF 50mm f/1.2L')]:
            db.add(Photo(path_hash=str(i), path=f'/photos/2026/{i}.cr3', filename=f'{i}.cr3', size=42, mtime_ns=1, camera=camera,lens=lens))


def test_auth_and_csrf():
    client = create_app().test_client()
    assert client.get('/api/photos').status_code == 401
    assert client.get('/media/1/preview').status_code == 401
    assert client.post('/api/login',json={'password':'test-password-long'}).status_code == 403
    token = client.get('/api/session').json['csrf']
    assert client.post('/api/login',json={'password':'wrong'},headers={'X-CSRF-Token':token}).status_code == 401


def test_combined_filters_and_keyset(client):
    seed()
    args = {'camera':'Canon EOS R6','lens':'EF 50mm f/1.2L','limit':1}
    data = client.get('/api/photos',query_string=args).json
    assert data['total'] == 2 and len(data['items']) == 1
    second = client.get('/api/photos',query_string=args | {'after':data['next_cursor']}).json
    assert second['items'][0]['id'] != data['items'][0]['id'] and second['next_cursor'] is None
    assert client.get('/api/photos',query_string={'camera':'Leica M Monochrom','lens':'EF 50mm f/1.2L'}).json['total'] == 0
    facets = client.get('/api/facets',query_string={'camera':'Canon EOS R6'}).json
    assert len(facets['lens']) == 2
    assert client.get('/api/photos?q=%25').json['total'] == 0  # '%' is literal, not wildcard
    assert client.get('/api/photos?after=oops').status_code == 400


def test_job_queue_cancel_and_csrf(client):
    assert client.post('/api/scan').status_code == 403
    headers = {'X-CSRF-Token':client.csrf}
    assert client.post('/api/scan',json={},headers=headers).status_code == 202
    assert client.post('/api/scan',json={},headers=headers).status_code == 409
    assert client.post('/api/scan/cancel',headers=headers).status_code == 200
    assert client.get('/api/scan').json['scan']['cancel'] is True


def test_folder_browser_selection_and_thumbnail_purge(client, tmp_path, monkeypatch):
    root = tmp_path / 'photos'; root.mkdir()
    selected = root / '2026'; selected.mkdir()
    (selected / 'session').mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    (root / 'outside-link').symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv('PHOTO_ROOT', str(root))
    cache = tmp_path / 'cache'; cache.mkdir()
    monkeypatch.setenv('CACHE_DIR', str(cache))
    headers = {'X-CSRF-Token':client.csrf}

    data = client.get('/api/folders').json
    assert [item['name'] for item in data['directories']] == ['2026']
    assert client.get('/api/folders', query_string={'path':'../outside'}).status_code == 400
    assert client.get('/api/folders', query_string={'path':'outside-link'}).status_code == 400

    response = client.post('/api/folders/select', json={'path':'2026'}, headers=headers)
    assert response.status_code == 200
    assert response.json['selected_display'] == '/photos/2026'
    assert client.get('/api/scan').json['root'] == '/photos/2026'
    browse = client.get('/api/folders', query_string={'path':'2026'}).json
    assert browse['parent'] == '' and browse['directories'][0]['path'] == '2026/session'

    key = 'a' * 64
    legacy_thumb = cache_file(cache, key, 'thumb')
    configured_thumb = cache_file(cache / 'thumbnails', 'b' * 64, 'thumb')
    preview = cache_file(cache, key, 'preview')
    for path, data in [(legacy_thumb,b'legacy-thumb'), (configured_thumb,b'new-thumb'), (preview,b'preview')]:
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    purged = client.delete('/api/cache/thumbnails', headers=headers)
    assert purged.status_code == 200 and purged.json['removed'] == 2
    assert not legacy_thumb.exists() and not configured_thumb.exists() and preview.exists()

    with Session.begin() as db:
        db.add(Scan(state='running'))
    assert client.post('/api/folders/select', json={'path':''}, headers=headers).status_code == 409
    assert client.delete('/api/cache/thumbnails', headers=headers).status_code == 409


def test_thumbnail_settings_stats_and_rebuild_queue(client, tmp_path, monkeypatch):
    cache = tmp_path / 'cache'
    monkeypatch.setenv('CACHE_DIR', str(cache))
    headers = {'X-CSRF-Token':client.csrf}
    with Session.begin() as db:
        for i in range(2):
            db.add(Photo(path_hash=f'cache-{i}', path=f'/photos/{i}.cr3', filename=f'{i}.cr3',
                         size=10, mtime_ns=1, cache_key=str(i + 1) * 64))

    initial = client.get('/api/settings/thumbnails').json
    assert initial['folder'] == 'thumbnails'
    assert initial['eligible_photos'] == 2
    assert initial['estimated_bytes'] == 2 * 60 * 1024
    assert initial['files'] == 0

    assert client.put('/api/settings/thumbnails', json={'folder':'/tmp/thumbs'}, headers=headers).status_code == 400
    assert client.put('/api/settings/thumbnails', json={'folder':'../thumbs'}, headers=headers).status_code == 400
    changed = client.put('/api/settings/thumbnails', json={'folder':'cache/thumbs'}, headers=headers)
    assert changed.status_code == 200 and changed.json['folder'] == 'cache/thumbs'
    assert Path(changed.json['path']).is_dir()

    root = resolve_thumbnail_root('cache/thumbs', create=True)
    first = cache_file(root, '1' * 64, 'thumb'); second = cache_file(root, '2' * 64, 'thumb')
    first.parent.mkdir(parents=True, exist_ok=True); second.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b'x' * 100); second.write_bytes(b'y' * 300)
    stats = client.get('/api/settings/thumbnails').json
    assert stats['files'] == 2 and stats['bytes'] == 400
    assert stats['estimated_per_thumbnail'] == 200 and stats['estimated_bytes'] == 400
    assert stats['estimate_basis'] == 'current average'

    queued = client.post('/api/cache/thumbnails/rebuild', headers=headers)
    assert queued.status_code == 202
    jid = queued.json['scan']['id']
    with Session() as db:
        assert db.get(Setting, f'scan_mode:{jid}').value == 'thumbnails'
    assert client.put('/api/settings/thumbnails', json={'folder':'other'}, headers=headers).status_code == 409
    assert client.delete('/api/cache/thumbnails', headers=headers).status_code == 409


def test_worker_uses_selected_subfolder(tmp_path, monkeypatch):
    root = tmp_path / 'photos'; root.mkdir()
    selected = root / 'selected'; selected.mkdir()
    monkeypatch.setattr(worker, 'ROOT', root)
    with Session.begin() as db:
        db.add(Setting(key='selected_photo_folder', value='selected'))
    assert worker.selected_scan_root() == selected.resolve()


def fake_previews(path, meta, cache, key, thumbnail_cache=None):
    for kind, root in [('preview', cache), ('thumb', thumbnail_cache or cache)]:
        p = cache_file(root, key, kind)
        p.parent.mkdir(parents=True,exist_ok=True)
        Image.new('RGB',(30,20),'red').save(p,'JPEG')


def test_recursive_incremental_and_preview_retry(tmp_path, monkeypatch):
    root = tmp_path/'photos'; root.mkdir(); nested = root/'nested'; nested.mkdir()
    photo = nested/'IMG_1.CR3'; photo.write_bytes(b'raw-fixture')
    (nested/'ignore.txt').write_text('ignored')
    (nested/'loop').symlink_to(root, target_is_directory=True)
    (nested/'link.cr3').symlink_to(photo)
    cache = tmp_path/'cache'
    monkeypatch.setattr(worker,'ROOT',root)
    monkeypatch.setattr(worker,'CACHE',str(cache))
    monkeypatch.setenv('CACHE_DIR',str(cache))
    calls=[]
    def metadata(paths):
        calls.extend(paths)
        return {str(p):{'Model':'Canon EOS R6','Make':'Canon','LensModel':'RF50mm F1.8 STM','DateTimeOriginal':'2026:09:06 12:00:00'} for p in paths}
    monkeypatch.setattr(worker,'metadata_batch',metadata)
    monkeypatch.setattr(worker,'make_previews',fake_previews)
    def run():
        with Session.begin() as db:
            j=Scan(); db.add(j); db.flush(); jid=j.id
        worker.scan(jid)
        with Session() as db:
            return db.get(Scan,jid)
    first=run()
    assert (first.state,first.discovered,first.indexed,first.errors)==('done',1,1,0)
    second=run()
    assert second.skipped==1 and len(calls)==1
    with Session() as db:
        p=db.query(Photo).one()
        assert p.camera=='Canon EOS R6' and p.taken_at.year==2026
        cache_file(cache/'thumbnails',p.cache_key,'thumb').unlink()
    assert run().indexed==1  # Missing cached thumbnail is repaired.
    photo.write_bytes(b'changed-raw-fixture')
    assert run().indexed==1
    with Session() as db:
        assert db.query(Photo).count()==1
    def broken(*args):
        raise ValueError('unsupported fixture')
    monkeypatch.setattr(worker,'make_previews',broken)
    photo.write_bytes(b'changed-again')
    assert run().errors==1
    with Session() as db:
        p=db.query(Photo).one()
        assert p.cache_key is None and 'unsupported' in p.preview_error


def test_thumbnail_only_rebuild(tmp_path, monkeypatch):
    root = tmp_path / 'photos'; root.mkdir()
    original = root / 'one.cr3'; original.write_bytes(b'raw')
    cache = tmp_path / 'cache'
    monkeypatch.setenv('CACHE_DIR', str(cache))
    monkeypatch.setattr(worker, 'CACHE', str(cache))
    key = 'c' * 64
    with Session.begin() as db:
        db.add(Photo(path_hash='rebuild', path=str(original), filename=original.name, size=3,
                     mtime_ns=1, cache_key=key, metadata_json={'Orientation':1}))
        job = Scan(); db.add(job); db.flush(); jid = job.id
    def fake_thumbnail(path, metadata, thumbnail_cache, cache_key):
        output = cache_file(thumbnail_cache, cache_key, 'thumb')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'thumb')
    monkeypatch.setattr(worker, 'make_thumbnail', fake_thumbnail)
    worker.rebuild_thumbnails(jid)
    with Session() as db:
        job = db.get(Scan, jid)
        assert (job.state, job.discovered, job.indexed, job.errors) == ('done', 1, 1, 0)
    assert cache_file(cache/'thumbnails', key, 'thumb').read_bytes() == b'thumb'


def test_missing_root_fails_and_keeps_catalog(tmp_path,monkeypatch):
    seed(); monkeypatch.setattr(worker,'ROOT',tmp_path/'unmounted')
    cache = tmp_path/'cache'; monkeypatch.setenv('CACHE_DIR', str(cache)); monkeypatch.setattr(worker,'CACHE',str(cache))
    with Session.begin() as db:
        j=Scan(); db.add(j); db.flush(); jid=j.id
    worker.scan(jid)
    with Session() as db:
        assert db.get(Scan,jid).state=='failed'
        assert db.query(Photo).count()==4


def test_image_orientation_cache_and_protected_media(client,tmp_path,monkeypatch):
    import app.imaging as imaging
    image=Image.new('RGB',(90,60),'red')
    assert orient(image,6).size==(60,90)
    monkeypatch.setattr(imaging,'open_preview',lambda path,meta:orient(image.copy(),6))
    monkeypatch.setenv('CACHE_DIR',str(tmp_path))
    key=hashlib.sha256(b'test').hexdigest()
    make_previews(Path('unused'),{},str(tmp_path),key)
    with Image.open(cache_file(tmp_path,key,'preview')) as result:
        assert result.size==(60,90) and result.mode=='RGB'
    with Session.begin() as db:
        p=Photo(path_hash='preview',path='/photos/test.cr3',filename='test.cr3',size=4,mtime_ns=1,cache_key=key)
        db.add(p); db.flush(); pid=p.id
    response=client.get(f'/media/{pid}/preview')
    assert response.status_code==200 and response.mimetype=='image/jpeg'
    assert response.headers['Cache-Control'].startswith('private')
    assert client.get(f'/media/{pid}/original').status_code==404
