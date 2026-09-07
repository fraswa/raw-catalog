import hashlib
import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

_temp = tempfile.TemporaryDirectory()
_base = Path(_temp.name)
os.environ['DATABASE_URL'] = 'sqlite:///' + str(_base / 'test.db')
os.environ['SECRET_KEY'] = 'test-secret-key-with-more-than-thirty-two-characters'
os.environ['CATALOG_PASSWORD'] = 'test-password-long'
os.environ['CACHE_DIR'] = str(_base / 'cache')
os.environ['EXTERNAL_STORAGE_ROOT'] = str(_base / 'storage')
os.environ['PREVIEW_EDGE'] = '2560'

from app.db import Base, engine, Session, Photo, Scan, Setting, init_db
from app.web import create_app
from app import worker
from app.imaging import orient, make_previews, cache_file
from app.storage import resolve_thumbnail_root, resolve_preview_root


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(engine)
    init_db()
    Path(os.environ['CACHE_DIR']).mkdir(parents=True, exist_ok=True)
    Path(os.environ['EXTERNAL_STORAGE_ROOT']).mkdir(parents=True, exist_ok=True)


@pytest.fixture
def client():
    app = create_app(); app.config['TESTING'] = True
    client = app.test_client()
    token = client.get('/api/session').json['csrf']
    response = client.post('/api/login', json={'password':'test-password-long'}, headers={'X-CSRF-Token':token})
    client.csrf = response.json['csrf']
    return client


def headers(client):
    return {'X-CSRF-Token':client.csrf}


def seed_dates():
    rows = [
        ('1','Canon EOS R6','EF 50mm f/1.2L','2024-01-10',{'FocalLength':'50 mm','FNumber':1.2}),
        ('2','Canon EOS R6','EF 24-70mm f/2.8L II','2025-06-15',{'FocalLength':'35 mm','FNumber':2.8}),
        ('3','Leica M Monochrom','Summilux 35mm','2023-03-20',{'FocalLength':'35 mm','FNumber':2.0}),
        ('4','Canon EOS R6','EF 50mm f/1.2L','2025-12-01',{'FocalLength':'50 mm','FNumber':1.4}),
    ]
    with Session.begin() as db:
        for path_hash,camera,lens,date,meta in rows:
            db.add(Photo(path_hash=path_hash,path=f'/photos/{path_hash}.cr3',filename=f'{path_hash}.cr3',size=42,
                         mtime_ns=1,camera=camera,lens=lens,taken_at=datetime.fromisoformat(date),metadata_json=meta))


def test_auth_and_csrf():
    client = create_app().test_client()
    assert client.get('/api/photos').status_code == 401
    assert client.get('/media/1/preview').status_code == 401
    assert client.post('/api/login',json={'password':'test-password-long'}).status_code == 403
    token = client.get('/api/session').json['csrf']
    assert client.post('/api/login',json={'password':'wrong'},headers={'X-CSRF-Token':token}).status_code == 401


def test_combined_filters_date_search_sort_and_pagination(client):
    seed_dates()
    args={'camera':'Canon EOS R6','lens':'EF 50mm f/1.2L','limit':1}
    first=client.get('/api/photos',query_string=args).json
    assert first['total']==2 and len(first['items'])==1
    second=client.get('/api/photos',query_string=args|{'after':first['next_cursor']}).json
    assert second['items'][0]['id']!=first['items'][0]['id'] and second['next_cursor'] is None

    ranged=client.get('/api/photos',query_string={'date_from':'2025-01-01','date_to':'2025-12-31','sort':'date_asc'}).json
    assert ranged['total']==2
    assert [p['taken_at'][:10] for p in ranged['items']]==['2025-06-15','2025-12-01']
    newest=client.get('/api/photos',query_string={'sort':'date_desc'}).json
    assert newest['items'][0]['taken_at'][:10]=='2025-12-01'
    assert client.get('/api/photos',query_string={'date_from':'2026-01-01','date_to':'2025-01-01'}).status_code==400
    assert client.get('/api/photos',query_string={'sort':'bad'}).status_code==400
    assert client.get('/api/photos?after=oops').status_code==400
    facets=client.get('/api/facets',query_string={'camera':'Canon EOS R6','date_from':'2025-01-01'}).json
    assert len(facets['lens'])==2


def test_job_queue_cancel_and_csrf(client):
    assert client.post('/api/scan').status_code==403
    h=headers(client)
    assert client.post('/api/scan',json={},headers=h).status_code==202
    assert client.post('/api/scan',json={},headers=h).status_code==409
    assert client.post('/api/scan/cancel',headers=h).status_code==200
    assert client.get('/api/scan').json['scan']['cancel'] is True


def test_folder_browser_selection(client,tmp_path,monkeypatch):
    root=tmp_path/'photos';root.mkdir();selected=root/'2026';selected.mkdir();(selected/'session').mkdir()
    outside=tmp_path/'outside';outside.mkdir();(root/'outside-link').symlink_to(outside,target_is_directory=True)
    monkeypatch.setenv('PHOTO_ROOT',str(root))
    data=client.get('/api/folders').json
    assert [x['name'] for x in data['directories']]==['2026']
    assert client.get('/api/folders',query_string={'path':'../outside'}).status_code==400
    assert client.get('/api/folders',query_string={'path':'outside-link'}).status_code==400
    response=client.post('/api/folders/select',json={'path':'2026'},headers=headers(client))
    assert response.status_code==200 and response.json['selected_display']=='/photos/2026'
    assert client.get('/api/scan').json['root']=='/photos/2026'


def test_external_storage_paths_preview_size_stats_and_purge(client,tmp_path,monkeypatch):
    cache=tmp_path/'cache';external=tmp_path/'mounted-share';cache.mkdir();external.mkdir()
    monkeypatch.setenv('CACHE_DIR',str(cache));monkeypatch.setenv('EXTERNAL_STORAGE_ROOT',str(external))
    h=headers(client)
    with Session.begin() as db:
        for i in range(2):
            db.add(Photo(path_hash=f'media-{i}',path=f'/photos/{i}.cr3',filename=f'{i}.cr3',size=10,mtime_ns=1,cache_key=str(i+1)*64))

    thumbs=client.get('/api/settings/thumbnails').json
    previews=client.get('/api/settings/previews').json
    assert thumbs['folder']==str(cache/'thumbnails')
    assert previews['folder']==str(cache/'previews') and previews['preview_edge']==2560
    assert thumbs['external_root']==str(external)
    assert client.put('/api/settings/thumbnails',json={'folder':'/tmp/nope'},headers=h).status_code==400
    assert client.put('/api/settings/previews',json={'folder':'../nope','preview_edge':2560},headers=h).status_code==400

    thumb_dest=external/'catalog'/'thumbs';preview_dest=external/'catalog'/'previews'
    changed_t=client.put('/api/settings/thumbnails',json={'folder':str(thumb_dest)},headers=h)
    changed_p=client.put('/api/settings/previews',json={'folder':str(preview_dest),'preview_edge':3840},headers=h)
    assert changed_t.status_code==200 and Path(changed_t.json['path']).is_dir()
    assert changed_p.status_code==200 and changed_p.json['preview_edge']==3840 and Path(changed_p.json['path']).is_dir()
    with Session() as db:
        assert db.get(Setting,'preview_edge').value=='3840'

    t1=cache_file(thumb_dest,'1'*64,'thumb');t2=cache_file(thumb_dest,'2'*64,'thumb')
    p1=cache_file(preview_dest,'1'*64,'preview');p2=cache_file(preview_dest,'2'*64,'preview')
    for path,size in [(t1,100),(t2,300),(p1,1000),(p2,3000)]: path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'x'*size)
    ts=client.get('/api/settings/thumbnails').json;ps=client.get('/api/settings/previews').json
    assert ts['files']==2 and ts['estimated_per_thumbnail']==200 and ts['estimated_bytes']==400
    assert ps['files']==2 and ps['estimated_per_preview']==2000 and ps['estimated_bytes']==4000

    legacy_t=cache_file(cache,'a'*64,'thumb');legacy_p=cache_file(cache,'b'*64,'preview')
    legacy_t.parent.mkdir(parents=True,exist_ok=True);legacy_t.write_bytes(b'old-thumb')
    legacy_p.parent.mkdir(parents=True,exist_ok=True);legacy_p.write_bytes(b'old-preview')
    purged_t=client.delete('/api/cache/thumbnails',headers=h)
    assert purged_t.status_code==200 and purged_t.json['removed']>=2
    assert not t1.exists() and p1.exists() and legacy_p.exists()
    purged_p=client.delete('/api/cache/previews',headers=h)
    assert purged_p.status_code==200 and purged_p.json['removed']>=2
    assert not p1.exists()

    queued=client.post('/api/cache/previews/rebuild',headers=h)
    assert queued.status_code==202
    jid=queued.json['scan']['id']
    with Session() as db: assert db.get(Setting,f'scan_mode:{jid}').value=='previews'
    assert client.put('/api/settings/previews',json={'folder':str(preview_dest),'preview_edge':5120},headers=h).status_code==409


def test_statistics_aggregation_and_cache(client):
    seed_dates()
    first=client.get('/api/statistics').json
    assert first['total_photos']==4 and first['dated_photos']==4
    assert first['distinct_cameras']==2 and first['distinct_lenses']==3
    assert first['cameras'][0]=={'value':'Canon EOS R6','count':3}
    focal={row['value']:row['count'] for row in first['focal_lengths']}
    aperture={row['value']:row['count'] for row in first['apertures']}
    assert focal['50 mm']==2 and focal['35 mm']==2
    assert aperture['f/1.2']==1 and aperture['f/2.8']==1
    assert len(first['trend'])>=3 and first['yearly'][-1]['year']=='2025'
    second=client.get('/api/statistics').json
    assert second['cached'] is True
    with Session.begin() as db:
        db.add(Photo(path_hash='newstat',path='/photos/new.cr3',filename='new.cr3',size=1,mtime_ns=1,camera='Nikon D3',lens='50mm',taken_at=datetime(2026,1,1),metadata_json={'FocalLength':'50 mm','FNumber':'4'}))
    refreshed=client.get('/api/statistics').json
    assert refreshed['total_photos']==5 and refreshed['cached'] is False


def test_worker_uses_selected_subfolder(tmp_path,monkeypatch):
    root=tmp_path/'photos';root.mkdir();selected=root/'selected';selected.mkdir();monkeypatch.setattr(worker,'ROOT',root)
    with Session.begin() as db: db.add(Setting(key='selected_photo_folder',value='selected'))
    assert worker.selected_scan_root()==selected.resolve()


def fake_previews(path,meta,preview_cache,key,thumbnail_cache=None,preview_edge=None):
    for kind,root in [('preview',preview_cache),('thumb',thumbnail_cache or preview_cache)]:
        p=cache_file(root,key,kind);p.parent.mkdir(parents=True,exist_ok=True);Image.new('RGB',(30,20),'red').save(p,'JPEG')


def test_recursive_incremental_and_generated_media_retry(tmp_path,monkeypatch):
    root=tmp_path/'photos';root.mkdir();nested=root/'nested';nested.mkdir();photo=nested/'IMG_1.CR3';photo.write_bytes(b'raw-fixture')
    (nested/'ignore.txt').write_text('ignored');(nested/'loop').symlink_to(root,target_is_directory=True);(nested/'link.cr3').symlink_to(photo)
    cache=tmp_path/'cache';external=tmp_path/'storage';external.mkdir()
    monkeypatch.setattr(worker,'ROOT',root);monkeypatch.setattr(worker,'CACHE',str(cache));monkeypatch.setenv('CACHE_DIR',str(cache));monkeypatch.setenv('EXTERNAL_STORAGE_ROOT',str(external))
    calls=[]
    def metadata(paths):
        calls.extend(paths);return {str(p):{'Model':'Canon EOS R6','Make':'Canon','LensModel':'RF50mm F1.8 STM','DateTimeOriginal':'2026:09:06 12:00:00','FocalLength':'50 mm','FNumber':1.8} for p in paths}
    monkeypatch.setattr(worker,'metadata_batch',metadata);monkeypatch.setattr(worker,'make_previews',fake_previews)
    def run():
        with Session.begin() as db: j=Scan();db.add(j);db.flush();jid=j.id
        worker.scan(jid)
        with Session() as db:return db.get(Scan,jid)
    first=run();assert (first.state,first.discovered,first.indexed,first.errors)==('done',1,1,0)
    second=run();assert second.skipped==1 and len(calls)==1
    with Session() as db:
        p=db.query(Photo).one();assert p.camera=='Canon EOS R6' and p.taken_at.year==2026
        cache_file(cache/'thumbnails',p.cache_key,'thumb').unlink()
    assert run().indexed==1


def test_media_rebuilds_use_configured_paths_and_edge(tmp_path,monkeypatch):
    photo_root=tmp_path/'photos';photo_root.mkdir();original=photo_root/'one.cr3';original.write_bytes(b'raw')
    cache=tmp_path/'cache';external=tmp_path/'share';external.mkdir();monkeypatch.setenv('CACHE_DIR',str(cache));monkeypatch.setenv('EXTERNAL_STORAGE_ROOT',str(external));monkeypatch.setattr(worker,'CACHE',str(cache))
    thumb_root=external/'thumbs';preview_root=external/'previews';key='c'*64
    with Session.begin() as db:
        db.add(Setting(key='thumbnail_folder',value=str(thumb_root)));db.add(Setting(key='preview_folder',value=str(preview_root)));db.add(Setting(key='preview_edge',value='3840'))
        db.add(Photo(path_hash='rebuild',path=str(original),filename=original.name,size=3,mtime_ns=1,cache_key=key,metadata_json={'Orientation':1}))
        jt=Scan();db.add(jt);db.flush();thumb_job=jt.id
        jp=Scan();db.add(jp);db.flush();preview_job=jp.id
    def fake_thumb(path,metadata,root,cache_key):
        out=cache_file(root,cache_key,'thumb');out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(b'thumb')
    edge_seen=[]
    def fake_preview(path,metadata,root,cache_key,edge=None):
        edge_seen.append(edge);out=cache_file(root,cache_key,'preview');out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(b'preview')
    monkeypatch.setattr(worker,'make_thumbnail',fake_thumb);monkeypatch.setattr(worker,'make_preview',fake_preview)
    worker.rebuild_thumbnails(thumb_job);worker.rebuild_previews(preview_job)
    assert cache_file(thumb_root,key,'thumb').read_bytes()==b'thumb'
    assert cache_file(preview_root,key,'preview').read_bytes()==b'preview' and edge_seen==[3840]


def test_missing_root_fails_and_keeps_catalog(tmp_path,monkeypatch):
    seed_dates();monkeypatch.setattr(worker,'ROOT',tmp_path/'unmounted')
    cache=tmp_path/'cache';storage=tmp_path/'storage';storage.mkdir();monkeypatch.setenv('CACHE_DIR',str(cache));monkeypatch.setenv('EXTERNAL_STORAGE_ROOT',str(storage));monkeypatch.setattr(worker,'CACHE',str(cache))
    with Session.begin() as db:j=Scan();db.add(j);db.flush();jid=j.id
    worker.scan(jid)
    with Session() as db:assert db.get(Scan,jid).state=='failed' and db.query(Photo).count()==4


def test_image_orientation_and_legacy_media_fallback(client,tmp_path,monkeypatch):
    import app.imaging as imaging
    image=Image.new('RGB',(90,60),'red');assert orient(image,6).size==(60,90)
    monkeypatch.setattr(imaging,'open_preview',lambda path,meta:orient(image.copy(),6))
    cache=tmp_path/'cache';storage=tmp_path/'storage';storage.mkdir();monkeypatch.setenv('CACHE_DIR',str(cache));monkeypatch.setenv('EXTERNAL_STORAGE_ROOT',str(storage))
    key=hashlib.sha256(b'test').hexdigest();make_previews(Path('unused'),{},str(cache),key,str(cache/'thumbnails'),1920)
    with Image.open(cache_file(cache,key,'preview')) as result: assert result.size==(60,90) and result.mode=='RGB'
    with Session.begin() as db:
        p=Photo(path_hash='preview',path='/photos/test.cr3',filename='test.cr3',size=4,mtime_ns=1,cache_key=key);db.add(p);db.flush();pid=p.id
    response=client.get(f'/media/{pid}/preview');assert response.status_code==200 and response.mimetype=='image/jpeg'
    assert response.headers['Cache-Control'].startswith('private') and client.get(f'/media/{pid}/original').status_code==404
