import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + str(Path(_temp.name) / 'statistics-technical.db'))
os.environ.setdefault('SECRET_KEY', 'test-secret-key-with-more-than-thirty-two-characters')
os.environ.setdefault('CATALOG_PASSWORD', 'test-password-long')

from app.db import Base, engine, Session, Photo, init_db
from app.web import create_app
from app.imaging import TAGS


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


def test_indexer_requests_technical_exif_fields():
    for tag in ('LensMake', 'LensMount', 'SensorSize', 'SensorWidth', 'SensorHeight',
                'ExifImageWidth', 'ExifImageHeight', 'FocalPlaneXResolution',
                'FocalPlaneYResolution', 'FocalPlaneResolutionUnit', 'SensorType', 'MaxApertureValue'):
        assert tag in TAGS


def test_technical_statistics_and_reference_breakdowns(client):
    with Session.begin() as db:
        db.add(Photo(
            path_hash='r6-rf', path='/photos/r6-rf.cr3', filename='r6-rf.cr3', size=1,
            mtime_ns=1, camera='Canon EOS R6', lens='RF50mm F1.8 STM',
            taken_at=datetime(2025, 1, 1), metadata_json={
                'Make': 'Canon', 'LensMake': 'Canon', 'ImageWidth': 6000, 'ImageHeight': 4000,
                'SensorSize': '35.9 x 23.9 mm', 'LensMount': 'Canon RF',
                'FocalLength': '50 mm', 'FNumber': 1.8,
            }))
        db.add(Photo(
            path_hash='r6-ef', path='/photos/r6-ef.cr3', filename='r6-ef.cr3', size=1,
            mtime_ns=1, camera='Canon EOS R6', lens='EF 24-70mm f/2.8L II USM',
            taken_at=datetime(2026, 1, 1), metadata_json={
                'Make': 'Canon', 'LensMake': 'Canon', 'ImageWidth': 6000, 'ImageHeight': 4000,
                'SensorWidth': 35.9, 'SensorHeight': 23.9, 'LensMount': 'Canon EF',
                'FocalLength': '35 mm', 'FNumber': 2.8,
            }))
        db.add(Photo(
            path_hash='d3', path='/photos/d3.nef', filename='d3.nef', size=1,
            mtime_ns=1, camera='Nikon D3', lens='AF-S NIKKOR 50mm f/1.4G',
            taken_at=datetime(2024, 1, 1), metadata_json={
                'Make': 'Nikon', 'LensMake': 'Nikon', 'ImageWidth': 4256, 'ImageHeight': 2832,
                'SensorSize': '36.0 x 23.9 mm', 'LensMount': 'Nikon F',
                'FocalLength': '50 mm', 'FNumber': 1.4,
            }))

    data = client.get('/api/statistics').json
    makers = {row['value']: row['count'] for row in data['makers']}
    megapixels = {row['value']: row['count'] for row in data['megapixels']}
    mounts = {row['value']: row['count'] for row in data['mounts']}
    sensors = {row['value']: row['count'] for row in data['sensor_sizes']}

    assert makers == {'Canon': 2, 'Nikon': 1}
    assert megapixels['24 MP'] == 2 and megapixels['12.1 MP'] == 1
    assert mounts == {'Canon RF': 1, 'Canon EF': 1, 'Nikon F': 1}
    assert sensors['35.9 × 23.9 mm'] == 2 and sensors['36 × 23.9 mm'] == 1
    assert data['maker_coverage'] == data['megapixel_coverage'] == 3
    assert data['sensor_coverage'] == data['mount_coverage'] == 3

    camera = data['camera_breakdowns']['Canon EOS R6']
    assert camera['maker'] == 'Canon'
    assert camera['megapixels'] == '24 MP'
    assert camera['sensor_size'] == '35.9 × 23.9 mm'
    assert camera['distinct_lenses'] == 2
    assert camera['active_years'] == '2025–2026'
    assert {row['value'] for row in camera['mounts']} == {'Canon RF', 'Canon EF'}

    lens = data['lens_breakdowns']['RF50mm F1.8 STM']
    assert lens['maker'] == 'Canon'
    assert lens['distinct_cameras'] == 1
    assert lens['mounts'][0]['value'] == 'Canon RF'
    assert lens['focal_lengths'][0]['value'] == '50 mm'


def test_statistics_exposes_complete_camera_and_lens_lists(client):
    with Session.begin() as db:
        for index in range(25):
            db.add(Photo(
                path_hash=f'full-list-{index}', path=f'/photos/full-{index}.dng',
                filename=f'full-{index}.dng', size=1, mtime_ns=index + 1,
                camera=f'Camera {index:02d}', lens=f'Lens {index:02d}',
                metadata_json={'Make': 'Test'},
            ))

    data = client.get('/api/statistics?refresh=1').json
    assert len(data['cameras']) == 25
    assert len(data['lenses']) == 25
    assert data['cameras'][0]['count'] == 1
    assert data['lenses'][0]['count'] == 1



def test_macos_resource_forks_are_recognized_as_garbage():
    from app.worker import is_macos_garbage_name, MACOS_GARBAGE_DIRS
    assert is_macos_garbage_name('._DSCF0214.dng')
    assert is_macos_garbage_name('.DS_Store')
    assert not is_macos_garbage_name('DSCF0214.dng')
    assert '.AppleDouble' in MACOS_GARBAGE_DIRS


def test_reference_database_rich_fields_and_mount_breakdown(client):
    with Session.begin() as db:
        db.add(Photo(path_hash='gear-r6', path='/photos/r6.cr3', filename='r6.cr3', size=1, mtime_ns=1,
                     camera='Canon EOS R6', lens='RF50mm F1.8 STM', taken_at=datetime(2026, 2, 1),
                     metadata_json={'Make':'Canon','LensMake':'Canon','ImageWidth':6000,'ImageHeight':4000,
                                    'LensMount':'Canon RF','FocalLength':'50 mm','FNumber':1.8}))
    headers={'X-CSRF-Token': client.csrf}
    response=client.put('/api/statistics/reference', json={'type':'camera','name':'Canon EOS R6',
        'sensor_size':'35.9 × 23.9 mm','sensor_type':'Full-frame CMOS','megapixels':'20.1 MP','mount':'Canon RF','notes':'Primary body'}, headers=headers)
    assert response.status_code == 200
    response=client.put('/api/statistics/reference', json={'type':'lens','name':'RF50mm F1.8 STM',
        'mount':'Canon RF','lens_type':'Prime','focal_range':'50 mm','max_aperture':'f/1.8'}, headers=headers)
    assert response.status_code == 200
    data=client.get('/api/statistics?refresh=1').json
    camera=data['camera_breakdowns']['Canon EOS R6']
    assert camera['sensor_type']=='Full-frame CMOS'
    assert camera['sensor_size']=='35.9 × 23.9 mm'
    assert data['mount_breakdowns']['Canon RF']['total_photos']==1
    assert data['mount_breakdowns']['Canon RF']['cameras'][0]['value']=='Canon EOS R6'
    lens=data['lens_breakdowns']['RF50mm F1.8 STM']
    assert lens['lens_type']=='Prime' and lens['focal_range']=='50 mm'
    database=client.get('/api/reference-database').json
    r6=next(row for row in database['cameras'] if row['name']=='Canon EOS R6')
    assert r6['override']['sensor_type']=='Full-frame CMOS'
    assert any(field['name']=='notes' for field in database['fields']['camera'])
