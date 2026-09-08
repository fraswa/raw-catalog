from pathlib import Path


def write(path, content):
    Path(path).write_text(content, encoding='utf-8')


def replace(path, old, new):
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'anchor not found in {path}: {old[:100]!r}')
    p.write_text(text.replace(old, new), encoding='utf-8')


# macOS metadata garbage: skip AppleDouble/resource-fork files and metadata directories early.
replace('app/worker.py',
"PROGRESS_INTERVAL = 0.5\n",
"PROGRESS_INTERVAL = 0.5\nMACOS_GARBAGE_DIRS = {'.AppleDouble', '__MACOSX', '.Spotlight-V100', '.Trashes', '.fseventsd'}\n\n\ndef is_macos_garbage_name(name):\n    return name == '.DS_Store' or name.startswith('._')\n")
replace('app/worker.py',
"directories[:] = [d for d in directories if not Path(directory, d).is_symlink()]\n",
"directories[:] = [d for d in directories\n                                   if d not in MACOS_GARBAGE_DIRS and not Path(directory, d).is_symlink()]\n")
replace('app/worker.py',
"                    path = Path(directory, filename)\n                    if path.suffix.lower() not in EXTENSIONS or path.is_symlink():\n",
"                    if is_macos_garbage_name(filename):\n                        continue\n                    path = Path(directory, filename)\n                    if path.suffix.lower() not in EXTENSIONS or path.is_symlink():\n")

# Request richer EXIF where available. Existing files do not need reindexing for manual overrides.
replace('app/imaging.py',
"        'ImageWidth', 'ImageHeight', 'ExifImageWidth', 'ExifImageHeight', 'SensorSize',\n",
"        'ImageWidth', 'ImageHeight', 'ExifImageWidth', 'ExifImageHeight', 'SensorSize', 'SensorType',\n")
replace('app/imaging.py',
"        'FocalPlaneResolutionUnit', 'Orientation', 'SerialNumber', 'LensSerialNumber', 'FileType']\n",
"        'FocalPlaneResolutionUnit', 'MaxApertureValue', 'Orientation', 'SerialNumber', 'LensSerialNumber', 'FileType']\n")

write('app/reference.py', r'''import hashlib
import json

from app.db import Setting

REFERENCE_OVERRIDES_KEY = 'catalog_reference_overrides_v1'
REFERENCE_FIELDS = {
    'camera': [
        {'name': 'maker', 'label': 'Maker', 'max_length': 190},
        {'name': 'mount', 'label': 'Lens mount', 'max_length': 190},
        {'name': 'sensor_size', 'label': 'Sensor size', 'max_length': 190},
        {'name': 'sensor_type', 'label': 'Sensor type / format', 'max_length': 190},
        {'name': 'megapixels', 'label': 'Resolution', 'max_length': 190},
        {'name': 'notes', 'label': 'Notes', 'max_length': 2000, 'multiline': True},
    ],
    'lens': [
        {'name': 'maker', 'label': 'Maker', 'max_length': 190},
        {'name': 'mount', 'label': 'Lens mount', 'max_length': 190},
        {'name': 'lens_type', 'label': 'Lens type', 'max_length': 190},
        {'name': 'focal_range', 'label': 'Focal range', 'max_length': 190},
        {'name': 'max_aperture', 'label': 'Maximum aperture', 'max_length': 190},
        {'name': 'notes', 'label': 'Notes', 'max_length': 2000, 'multiline': True},
    ],
}


def load_reference_overrides(db):
    setting = db.get(Setting, REFERENCE_OVERRIDES_KEY)
    if not setting or not setting.value:
        return {'camera': {}, 'lens': {}}
    try:
        value = json.loads(setting.value)
    except (TypeError, ValueError):
        return {'camera': {}, 'lens': {}}
    if not isinstance(value, dict):
        return {'camera': {}, 'lens': {}}
    result = {'camera': {}, 'lens': {}}
    for kind in result:
        bucket = value.get(kind, {})
        if isinstance(bucket, dict):
            result[kind] = {str(name): fields for name, fields in bucket.items() if isinstance(fields, dict)}
    return result


def reference_fingerprint(db):
    setting = db.get(Setting, REFERENCE_OVERRIDES_KEY)
    raw = setting.value if setting else ''
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def reference_field_schema(kind=None):
    if kind is None:
        return REFERENCE_FIELDS
    if kind not in REFERENCE_FIELDS:
        raise ValueError('Reference type must be camera or lens')
    return REFERENCE_FIELDS[kind]


def reference_field_names(kind):
    return {field['name'] for field in reference_field_schema(kind)}


def _field(value, label, max_length=190):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError(f'{label} must be text')
    value = value.strip()
    if len(value) > max_length or any(ord(char) < 32 and char not in '\n\t' for char in value):
        raise ValueError(f'Invalid {label.lower()}')
    return value


def save_reference_override(db, kind, name, changes):
    if kind not in REFERENCE_FIELDS:
        raise ValueError('Reference type must be camera or lens')
    name = _field(name, 'Name')
    if not name:
        raise ValueError('Reference name is required')
    if not isinstance(changes, dict):
        raise ValueError('Reference changes must be an object')
    definitions = {field['name']: field for field in REFERENCE_FIELDS[kind]}
    unknown = set(changes) - set(definitions)
    if unknown:
        raise ValueError('Unsupported reference field')
    overrides = load_reference_overrides(db)
    fields = dict(overrides[kind].get(name, {}))
    for key, raw in changes.items():
        definition = definitions[key]
        value = _field(raw, definition['label'], definition.get('max_length', 190))
        if value:
            fields[key] = value
        else:
            fields.pop(key, None)
    if fields:
        overrides[kind][name] = fields
    else:
        overrides[kind].pop(name, None)
    encoded = json.dumps(overrides, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    setting = db.get(Setting, REFERENCE_OVERRIDES_KEY)
    if setting is None:
        db.add(Setting(key=REFERENCE_OVERRIDES_KEY, value=encoded))
    else:
        setting.value = encoded
    return fields
''')

write('app/statistics.py', r'''import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from sqlalchemy import select, func

from app.db import Photo, Setting
from app.reference import load_reference_overrides, reference_fingerprint

CACHE_KEY = 'statistics_cache_v5'
_number = re.compile(r'[-+]?\d+(?:\.\d+)?')


def _number_value(value):
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    match = _number.search(str(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _numbers(value):
    if value is None:
        return []
    result = []
    for match in _number.findall(str(value)):
        try:
            result.append(float(match))
        except ValueError:
            pass
    return result


def _label_number(value, digits=1):
    if value is None:
        return None
    rounded = round(value, digits)
    return str(int(rounded)) if float(rounded).is_integer() else f'{rounded:g}'


def _top(counter, limit=20):
    return [{'value': value, 'count': count} for value, count in counter.most_common(limit)]


def _first(counter, default=None):
    return counter.most_common(1)[0][0] if counter else default


def _clean_text(value):
    value = str(value or '').strip()
    return None if not value or value.lower() in ('unknown', 'none', 'n/a', '0') else value


def _megapixels(metadata):
    width = _number_value(metadata.get('ImageWidth') or metadata.get('ExifImageWidth'))
    height = _number_value(metadata.get('ImageHeight') or metadata.get('ExifImageHeight'))
    if not width or not height or width < 100 or height < 100:
        return None
    value = width * height / 1_000_000
    if not 0.1 <= value <= 500:
        return None
    return f'{_label_number(value, 1)} MP'


def _sensor_size(metadata):
    explicit = metadata.get('SensorSize')
    values = _numbers(explicit)
    if len(values) >= 2:
        width, height = values[0], values[1]
        if 2 <= width <= 100 and 2 <= height <= 100:
            return f'{_label_number(width, 1)} × {_label_number(height, 1)} mm'
    width = _number_value(metadata.get('SensorWidth'))
    height = _number_value(metadata.get('SensorHeight'))
    if width and height and 2 <= width <= 100 and 2 <= height <= 100:
        return f'{_label_number(width, 1)} × {_label_number(height, 1)} mm'
    px_w = _number_value(metadata.get('ImageWidth') or metadata.get('ExifImageWidth'))
    px_h = _number_value(metadata.get('ImageHeight') or metadata.get('ExifImageHeight'))
    x_res = _number_value(metadata.get('FocalPlaneXResolution'))
    y_res = _number_value(metadata.get('FocalPlaneYResolution'))
    unit = str(metadata.get('FocalPlaneResolutionUnit') or '').lower()
    factor = 25.4 if ('inch' in unit or unit.strip() == '2') else 10.0 if ('cm' in unit or unit.strip() == '3') else None
    if factor and px_w and px_h and x_res and y_res:
        width, height = px_w / x_res * factor, px_h / y_res * factor
        if 2 <= width <= 100 and 2 <= height <= 100:
            return f'{_label_number(width, 1)} × {_label_number(height, 1)} mm'
    return None


def _lens_mount(metadata, camera, lens):
    reported = _clean_text(metadata.get('LensMount') or metadata.get('Mount'))
    if reported:
        return reported
    text = str(lens or '').strip().upper()
    maker = str(metadata.get('Make') or '').strip().upper()
    if text.startswith('RF-S') or text.startswith('RF '): return 'Canon RF'
    if text.startswith('EF-S'): return 'Canon EF-S'
    if text.startswith('EF-M'): return 'Canon EF-M'
    if text.startswith('EF '): return 'Canon EF'
    if 'NIKKOR Z' in text: return 'Nikon Z'
    if maker.startswith('NIKON') and 'NIKKOR' in text: return 'Nikon F'
    if maker.startswith('SONY') and (text.startswith('FE ') or text.startswith('E ') or text.startswith('E PZ')): return 'Sony E'
    if maker.startswith('FUJIFILM') and (text.startswith('XF') or text.startswith('XC')): return 'Fujifilm X'
    if maker.startswith('FUJIFILM') and text.startswith('GF'): return 'Fujifilm G'
    if ('OLYMPUS' in maker or 'OM DIGITAL' in maker or 'PANASONIC' in maker) and 'MICRO FOUR THIRDS' in str(metadata.get('LensType') or '').upper():
        return 'Micro Four Thirds'
    return None


def _active_years(counter):
    if not counter:
        return None
    years = sorted(counter)
    return years[0] if len(years) == 1 else f'{years[0]}–{years[-1]}'


def _signature(db):
    count, latest = db.execute(select(func.count(Photo.id), func.max(Photo.indexed_at))).one()
    return f'{count}:{latest.isoformat() if latest else ""}:{reference_fingerprint(db)}'


def build_statistics(db, force=False):
    signature = _signature(db)
    cached = db.get(Setting, CACHE_KEY)
    if cached and not force:
        try:
            payload = json.loads(cached.value)
            if payload.get('signature') == signature:
                payload['cached'] = True
                return payload
        except (TypeError, ValueError):
            pass

    cameras = Counter(); lenses = Counter(); focals = Counter(); apertures = Counter(); months = Counter()
    makers = Counter(); lens_makers = Counter(); mounts = Counter(); sensor_sizes = Counter(); sensor_types = Counter(); megapixels = Counter()
    camera_months = defaultdict(Counter); camera_lenses = defaultdict(Counter); camera_focals = defaultdict(Counter)
    camera_apertures = defaultdict(Counter); camera_years = defaultdict(Counter); camera_makers = defaultdict(Counter)
    camera_mounts = defaultdict(Counter); camera_sensors = defaultdict(Counter); camera_sensor_types = defaultdict(Counter); camera_megapixels = defaultdict(Counter)
    lens_cameras = defaultdict(Counter); lens_years = defaultdict(Counter); lens_focals = defaultdict(Counter); lens_apertures = defaultdict(Counter)
    lens_mounts = defaultdict(Counter); lens_maker_counts = defaultdict(Counter); lens_type_counts = defaultdict(Counter); lens_max_apertures = defaultdict(Counter)
    mount_cameras = defaultdict(Counter); mount_lenses = defaultdict(Counter); mount_focals = defaultdict(Counter); mount_apertures = defaultdict(Counter)
    mount_years = defaultdict(Counter); mount_makers = defaultdict(Counter); mount_sensors = defaultdict(Counter); mount_sensor_types = defaultdict(Counter); mount_megapixels = defaultdict(Counter)
    mount_months = defaultdict(lambda: defaultdict(Counter)); camera_dated = Counter(); lens_dated = Counter(); mount_dated = Counter()
    dated = total = 0
    overrides = load_reference_overrides(db); camera_overrides = overrides.get('camera', {}); lens_overrides = overrides.get('lens', {})

    statement = select(Photo.camera, Photo.lens, Photo.taken_at, Photo.metadata_json).execution_options(yield_per=2000)
    for camera, lens, taken_at, metadata in db.execute(statement):
        total += 1; camera = camera or 'Unknown camera'; lens = lens or 'Unknown lens'; metadata = metadata or {}
        cameras[camera] += 1; lenses[lens] += 1; camera_lenses[camera][lens] += 1; lens_cameras[lens][camera] += 1
        co = camera_overrides.get(camera, {}); lo = lens_overrides.get(lens, {})
        maker = _clean_text(co.get('maker')) or _clean_text(metadata.get('Make'))
        lens_maker = _clean_text(lo.get('maker')) or _clean_text(metadata.get('LensMake'))
        megapixel = _clean_text(co.get('megapixels')) or _megapixels(metadata)
        sensor = _clean_text(co.get('sensor_size')) or _sensor_size(metadata)
        sensor_type = _clean_text(co.get('sensor_type')) or _clean_text(metadata.get('SensorType'))
        lens_type = _clean_text(lo.get('lens_type')) or _clean_text(metadata.get('LensType'))
        max_aperture = _clean_text(lo.get('max_aperture')) or _clean_text(metadata.get('MaxApertureValue'))
        mount = _clean_text(lo.get('mount')) or _clean_text(co.get('mount')) or _lens_mount(metadata, camera, lens)
        if maker: makers[maker] += 1; camera_makers[camera][maker] += 1
        if lens_maker: lens_makers[lens_maker] += 1; lens_maker_counts[lens][lens_maker] += 1
        if megapixel: megapixels[megapixel] += 1; camera_megapixels[camera][megapixel] += 1
        if sensor: sensor_sizes[sensor] += 1; camera_sensors[camera][sensor] += 1
        if sensor_type: sensor_types[sensor_type] += 1; camera_sensor_types[camera][sensor_type] += 1
        if lens_type: lens_type_counts[lens][lens_type] += 1
        if max_aperture: lens_max_apertures[lens][max_aperture] += 1
        if mount:
            mounts[mount] += 1; camera_mounts[camera][mount] += 1; lens_mounts[lens][mount] += 1
            mount_cameras[mount][camera] += 1; mount_lenses[mount][lens] += 1
            if maker: mount_makers[mount][maker] += 1
            if sensor: mount_sensors[mount][sensor] += 1
            if sensor_type: mount_sensor_types[mount][sensor_type] += 1
            if megapixel: mount_megapixels[mount][megapixel] += 1
        focal = _number_value(metadata.get('FocalLength')); aperture = _number_value(metadata.get('FNumber'))
        if focal is not None and focal > 0:
            label = _label_number(focal, 1) + ' mm'; focals[label] += 1; camera_focals[camera][label] += 1; lens_focals[lens][label] += 1
            if mount: mount_focals[mount][label] += 1
        if aperture is not None and aperture > 0:
            label = 'f/' + _label_number(aperture, 1); apertures[label] += 1; camera_apertures[camera][label] += 1; lens_apertures[lens][label] += 1
            if mount: mount_apertures[mount][label] += 1
        if taken_at:
            dated += 1; camera_dated[camera] += 1; lens_dated[lens] += 1
            month = taken_at.strftime('%Y-%m'); year = taken_at.strftime('%Y'); months[month] += 1; camera_years[camera][year] += 1; lens_years[lens][year] += 1; camera_months[month][camera] += 1
            if mount:
                mount_dated[mount] += 1; mount_years[mount][year] += 1; mount_months[mount][month][camera] += 1

    month_keys = sorted(months)[-60:]; top_cameras = [name for name, _ in cameras.most_common(5)]; top_lenses = [name for name, _ in lenses.most_common(5)]
    trend = [{'month': month, 'total': months[month], 'cameras': {name: camera_months[month].get(name, 0) for name in top_cameras}} for month in month_keys]
    yearly = Counter()
    for month, count in months.items(): yearly[month[:4]] += count

    camera_breakdowns = {}
    for camera in cameras:
        override = camera_overrides.get(camera, {})
        camera_breakdowns[camera] = {
            'total_photos': cameras[camera], 'dated_photos': camera_dated[camera], 'distinct_lenses': len(camera_lenses[camera]),
            'maker': _first(camera_makers[camera]), 'megapixels': _first(camera_megapixels[camera]), 'sensor_size': _first(camera_sensors[camera]),
            'sensor_type': _first(camera_sensor_types[camera]), 'mounts': _top(camera_mounts[camera], 5), 'active_years': _active_years(camera_years[camera]),
            'lenses': _top(camera_lenses[camera], None), 'focal_lengths': _top(camera_focals[camera]), 'apertures': _top(camera_apertures[camera]),
            'yearly': [{'year': year, 'count': camera_years[camera][year]} for year in sorted(camera_years[camera])],
            'trend': [{'month': month, 'count': camera_months[month].get(camera, 0)} for month in month_keys],
            'override': override, 'override_maker': override.get('maker'), 'override_mount': override.get('mount'), 'notes': override.get('notes'),
        }

    lens_breakdowns = {}
    for lens in lenses:
        override = lens_overrides.get(lens, {})
        lens_breakdowns[lens] = {
            'total_photos': lenses[lens], 'dated_photos': lens_dated[lens], 'distinct_cameras': len(lens_cameras[lens]),
            'maker': _first(lens_maker_counts[lens]), 'mounts': _top(lens_mounts[lens], 5), 'lens_type': _first(lens_type_counts[lens]),
            'focal_range': override.get('focal_range'), 'max_aperture': _first(lens_max_apertures[lens]), 'active_years': _active_years(lens_years[lens]),
            'cameras': _top(lens_cameras[lens], None), 'focal_lengths': _top(lens_focals[lens]), 'apertures': _top(lens_apertures[lens]),
            'yearly': [{'year': year, 'count': lens_years[lens][year]} for year in sorted(lens_years[lens])],
            'override': override, 'override_maker': override.get('maker'), 'override_mount': override.get('mount'), 'notes': override.get('notes'),
        }

    mount_breakdowns = {}
    for mount in mounts:
        mount_top = [name for name, _ in mount_cameras[mount].most_common(5)]
        mount_breakdowns[mount] = {
            'mount': mount, 'total_photos': mounts[mount], 'dated_photos': mount_dated[mount],
            'distinct_cameras': len(mount_cameras[mount]), 'distinct_lenses': len(mount_lenses[mount]),
            'cameras': _top(mount_cameras[mount], None), 'lenses': _top(mount_lenses[mount], None),
            'focal_lengths': _top(mount_focals[mount]), 'apertures': _top(mount_apertures[mount]),
            'makers': _top(mount_makers[mount]), 'sensor_sizes': _top(mount_sensors[mount]), 'sensor_types': _top(mount_sensor_types[mount]), 'megapixels': _top(mount_megapixels[mount]),
            'maker_coverage': sum(mount_makers[mount].values()), 'sensor_coverage': sum(mount_sensors[mount].values()),
            'sensor_type_coverage': sum(mount_sensor_types[mount].values()), 'megapixel_coverage': sum(mount_megapixels[mount].values()),
            'yearly': [{'year': year, 'count': mount_years[mount][year]} for year in sorted(mount_years[mount])],
            'top_camera_names': mount_top,
            'trend': [{'month': month, 'cameras': {name: mount_months[mount][month].get(name, 0) for name in mount_top}} for month in month_keys],
        }

    payload = {
        'signature': signature, 'cached': False, 'generated_at': datetime.utcnow().isoformat() + 'Z',
        'total_photos': total, 'dated_photos': dated, 'undated_photos': total - dated,
        'distinct_cameras': len(cameras), 'distinct_lenses': len(lenses), 'distinct_focal_lengths': len(focals), 'distinct_apertures': len(apertures),
        'cameras': _top(cameras, None), 'lenses': _top(lenses, None), 'focal_lengths': _top(focals), 'apertures': _top(apertures),
        'makers': _top(makers), 'lens_makers': _top(lens_makers), 'mounts': _top(mounts, None), 'sensor_sizes': _top(sensor_sizes), 'sensor_types': _top(sensor_types), 'megapixels': _top(megapixels),
        'maker_coverage': sum(makers.values()), 'mount_coverage': sum(mounts.values()), 'sensor_coverage': sum(sensor_sizes.values()),
        'sensor_type_coverage': sum(sensor_types.values()), 'megapixel_coverage': sum(megapixels.values()),
        'top_camera_names': top_cameras, 'top_lens_names': top_lenses, 'trend': trend,
        'yearly': [{'year': year, 'count': yearly[year]} for year in sorted(yearly)],
        'camera_breakdowns': camera_breakdowns, 'lens_breakdowns': lens_breakdowns, 'mount_breakdowns': mount_breakdowns,
    }
    encoded = json.dumps(payload, separators=(',', ':'))
    setting = db.get(Setting, CACHE_KEY)
    if setting is None: db.add(Setting(key=CACHE_KEY, value=encoded))
    else: setting.value = encoded
    db.flush(); return payload
''')

# Web routes/API.
replace('app/web.py',
"from app.reference import save_reference_override\n",
"from app.reference import save_reference_override, reference_field_schema, reference_field_names\n")
replace('app/web.py',
"    @app.get('/statistics')\n    def statistics_page():\n        return app.send_static_file('statistics.html')\n\n",
"    @app.get('/statistics')\n    def statistics_page():\n        return app.send_static_file('statistics.html')\n\n    @app.get('/catalog')\n    def catalog_page():\n        return app.send_static_file('catalog.html')\n\n")
old_route = '''    @app.put('/api/statistics/reference')
    def update_statistics_reference():
        data = request.get_json(silent=True) or {}
        kind = data.get('type')
        name = data.get('name')
        if kind not in ('camera', 'lens') or not isinstance(name, str) or not name.strip():
            abort(400, 'Reference type and name are required')
        name = name.strip()[:190]
        column = Photo.camera if kind == 'camera' else Photo.lens
        with Session.begin() as db:
            if not db.scalar(select(func.count()).select_from(Photo).where(column == name)):
                abort(404, f'{kind.capitalize()} not found in catalog')
            try:
                saved = save_reference_override(db, kind, name, data.get('maker'), data.get('mount'))
            except ValueError as exc:
                abort(400, str(exc))
        return jsonify(ok=True, type=kind, name=name, override=saved)
'''
new_route = '''    @app.put('/api/statistics/reference')
    def update_statistics_reference():
        data = request.get_json(silent=True) or {}
        kind = data.get('type')
        name = data.get('name')
        if kind not in ('camera', 'lens') or not isinstance(name, str) or not name.strip():
            abort(400, 'Reference type and name are required')
        name = name.strip()[:190]
        column = Photo.camera if kind == 'camera' else Photo.lens
        try:
            allowed = reference_field_names(kind)
        except ValueError as exc:
            abort(400, str(exc))
        changes = {key: data[key] for key in allowed if key in data}
        if not changes:
            abort(400, 'At least one reference field is required')
        with Session.begin() as db:
            if not db.scalar(select(func.count()).select_from(Photo).where(column == name)):
                abort(404, f'{kind.capitalize()} not found in catalog')
            try:
                saved = save_reference_override(db, kind, name, changes)
            except ValueError as exc:
                abort(400, str(exc))
        return jsonify(ok=True, type=kind, name=name, override=saved)

    @app.get('/api/reference-database')
    def reference_database():
        with Session.begin() as db:
            stats = build_statistics(db, force=request.args.get('refresh') == '1')
        cameras = []
        for row in stats.get('cameras', []):
            detail = stats.get('camera_breakdowns', {}).get(row['value'], {})
            cameras.append({'name': row['value'], 'count': row['count'], 'override': detail.get('override', {}),
                            'facts': {'maker': detail.get('maker'), 'mount': (detail.get('mounts') or [{}])[0].get('value'),
                                      'sensor_size': detail.get('sensor_size'), 'sensor_type': detail.get('sensor_type'),
                                      'megapixels': detail.get('megapixels'), 'notes': detail.get('notes')}})
        lenses = []
        for row in stats.get('lenses', []):
            detail = stats.get('lens_breakdowns', {}).get(row['value'], {})
            lenses.append({'name': row['value'], 'count': row['count'], 'override': detail.get('override', {}),
                           'facts': {'maker': detail.get('maker'), 'mount': (detail.get('mounts') or [{}])[0].get('value'),
                                     'lens_type': detail.get('lens_type'), 'focal_range': detail.get('focal_range'),
                                     'max_aperture': detail.get('max_aperture'), 'notes': detail.get('notes')}})
        return jsonify(fields=reference_field_schema(), cameras=cameras, lenses=lenses,
                       mount_options=[row['value'] for row in stats.get('mounts', [])], generated_at=stats.get('generated_at'))
'''
replace('app/web.py', old_route, new_route)

# Complete statistics UI with mount scope/filter and richer reference facts.
write('app/static/statistics.html', r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><title>RAW Catalog Statistics</title><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/controls.css"><link rel="stylesheet" href="/static/statistics.css"><script defer src="/static/statistics.js"></script></head>
<body><div id="statisticsApp" hidden>
<header><div class="brand"><span class="mark">R / C</span><div><strong>RAW Catalog</strong><span>Statistics</span></div></div><div class="header-actions"><a class="button-link" href="/">Library</a><a class="button-link" href="/catalog">Gear database</a><a class="button-link" href="/indexer">Indexer</a><a class="button-link" href="/settings">Settings</a><button id="refreshStats">Refresh</button><button id="signOut">Sign out</button></div></header>
<main class="statistics-main"><div class="title-row"><div><p class="eyebrow">STATISTICS</p><h1>Archive usage</h1></div><span id="generatedAt" class="read-only">—</span></div>
<div id="statisticsError" class="error" role="alert" hidden></div><div id="statisticsLoading" class="empty"><h2>Building archive statistics…</h2><p>The first calculation can take longer on a large catalog. Results are cached until the catalog changes.</p></div>
<div id="statisticsContent" hidden>
<section class="stats-filter"><label for="mountFilter">Filter by lens mount</label><select id="mountFilter"><option value="">All lens mounts</option></select><span>Uses indexed EXIF/inference plus your Gear database overrides.</span></section>
<section id="cameraScope" class="scope-card" hidden><div><p class="eyebrow">CAMERA FILTER</p><strong id="cameraScopeName">—</strong><span id="cameraScopeSummary"></span></div><div class="scope-actions"><a id="cameraScopeLibrary" class="button-link" href="/">Open in Library</a><button id="clearCameraScope">All cameras</button></div></section>
<section id="mountScope" class="scope-card" hidden><div><p class="eyebrow">LENS MOUNT FILTER</p><strong id="mountScopeName">—</strong><span id="mountScopeSummary"></span></div><div class="scope-actions"><button id="clearMountScope">All mounts</button></div></section>
<section class="summary-grid"><div class="summary-card"><span id="summaryLabel1">Photographs</span><strong id="totalPhotos">—</strong></div><div class="summary-card"><span id="summaryLabel2">With capture date</span><strong id="datedPhotos">—</strong></div><div class="summary-card"><span id="summaryLabel3">Cameras</span><strong id="distinctCameras">—</strong></div><div class="summary-card"><span id="summaryLabel4">Lenses</span><strong id="distinctLenses">—</strong></div></section>
<section class="statistics-grid">
<div class="stats-card"><div class="card-heading"><p class="eyebrow">CAMERAS</p><h2>Camera usage</h2><p>Click a camera to drill down. Hover or focus for its catalog reference.</p></div><div id="cameraBars" class="bar-list"></div><label class="list-toggle"><input id="showAllCameras" type="checkbox"><span id="showAllCamerasLabel">Show all cameras</span></label></div>
<div class="stats-card"><div class="card-heading"><p class="eyebrow">LENSES</p><h2 id="lensHeading">Lens usage</h2><p>Click a lens to open its Library view. Hover or focus for catalog facts.</p></div><div id="lensBars" class="bar-list"></div><label class="list-toggle"><input id="showAllLenses" type="checkbox"><span id="showAllLensesLabel">Show all lenses</span></label></div>
<div class="stats-card"><div class="card-heading"><p class="eyebrow">FOCAL LENGTH</p><h2 id="focalHeading">Most-used focal lengths</h2></div><div id="focalBars" class="bar-list"></div></div>
<div class="stats-card"><div class="card-heading"><p class="eyebrow">APERTURE</p><h2 id="apertureHeading">Most-used apertures</h2></div><div id="apertureBars" class="bar-list"></div></div>
<div class="stats-card technical-card"><div class="card-heading"><p class="eyebrow">MAKERS</p><h2>Camera maker usage</h2><p id="makerCoverage"></p></div><div id="makerBars" class="bar-list"></div></div>
<div class="stats-card technical-card"><div class="card-heading"><p class="eyebrow">MEGAPIXELS</p><h2>Resolution usage</h2><p id="megapixelCoverage"></p></div><div id="megapixelBars" class="bar-list"></div></div>
<div class="stats-card technical-card"><div class="card-heading"><p class="eyebrow">SENSOR SIZE</p><h2>Sensor-size usage</h2><p id="sensorCoverage"></p></div><div id="sensorBars" class="bar-list"></div></div>
<div class="stats-card technical-card"><div class="card-heading"><p class="eyebrow">SENSOR TYPE</p><h2>Sensor type / format</h2><p id="sensorTypeCoverage"></p></div><div id="sensorTypeBars" class="bar-list"></div></div>
<div class="stats-card technical-card"><div class="card-heading"><p class="eyebrow">LENS MOUNT</p><h2>Lens-mount usage</h2><p id="mountCoverage"></p></div><div id="mountBars" class="bar-list"></div></div>
</section><p class="metadata-note">Gear database overrides affect catalog statistics only. Original RAW metadata remains untouched.</p>
<section class="stats-card wide-card"><div class="card-heading"><p class="eyebrow">TREND</p><h2 id="trendHeading">Camera usage — last 60 active months</h2></div><div id="trendLegend" class="trend-legend"></div><div class="chart-wrap"><svg id="cameraTrend" viewBox="0 0 1000 320"></svg></div><div id="trendDates" class="trend-dates"></div></section>
<section class="stats-card wide-card"><div class="card-heading"><p class="eyebrow">YEARS</p><h2 id="yearHeading">Photographs by year</h2></div><div id="yearBars" class="bar-list years"></div></section>
</div></main>
<aside id="referenceCard" class="reference-card" role="dialog" hidden><div id="referenceType" class="reference-type"></div><h3 id="referenceTitle"></h3><dl id="referenceFacts"></dl><div id="referenceFooter" class="reference-footer"></div><div id="referenceActions" class="reference-actions"><button id="editReference">Edit maker / mount</button><a class="button-link" href="/catalog">Full database editor</a></div><form id="referenceEditForm" class="reference-edit" hidden><label>Maker<input id="referenceMaker" maxlength="190"></label><label>Lens mount<input id="referenceMount" maxlength="190"></label><div class="reference-edit-actions"><button class="primary" type="submit">Save</button><button id="referenceClear" type="button">Use indexed maker/mount</button><button id="referenceCancel" type="button">Cancel</button></div><p id="referenceEditStatus"></p></form></aside>
<footer>RAW Catalog <span>Statistics use indexed metadata and optional catalog overrides</span></footer></div></body></html>
''')

write('app/static/statistics.js', r''''use strict';
const $=id=>document.getElementById(id); const nf=new Intl.NumberFormat();
let csrf='',archiveData=null,selectedCamera='',selectedMount='',referenceState=null,referenceHideTimer=null,referenceEditing=false;
async function api(url,options={}){const r=await fetch(url,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...(options.headers||{})}});const j=await r.json();if(!r.ok){if(r.status===401)location.href='/';throw new Error(j.error||`Request failed (${r.status})`)}return j}
function pct(v,t){return t?Math.round(v*1000/t)/10:0} function libraryUrl(f){return '/?'+new URLSearchParams(f)} function first(rows){return rows?.[0]?.value||null} function top(rows,n=2){return (rows||[]).slice(0,n).map(x=>x.value).join(', ')||null}
function showError(e){$('statisticsError').textContent=e.message;$('statisticsError').hidden=false}
const LIMIT=20; function rowsVisible(rows,id){return $(id).checked?(rows||[]): (rows||[]).slice(0,LIMIT)}
function syncToggle(id,label,noun,count){const c=$(id);c.disabled=count<=LIMIT;if(c.disabled)c.checked=false;$(label).textContent=count>LIMIT?`Show all ${noun} (${nf.format(count)})`:`All ${noun} shown (${nf.format(count)})`}
function mountDetail(){return selectedMount?archiveData?.mount_breakdowns?.[selectedMount]:null}
function renderBars(id,rows,opt={}){const host=$(id);host.replaceChildren();if(!rows?.length){const s=document.createElement('span');s.className='read-only';s.textContent='No metadata available';host.append(s);return}const max=Math.max(1,...rows.map(r=>r.count));for(const row of rows){const item=document.createElement('div');item.className='bar-item'+((opt.camera&&row.value===selectedCamera)||(opt.mount&&row.value===selectedMount)?' selected':'');let label;if(opt.camera){label=document.createElement('button');label.type='button';label.className='bar-filter-link';label.onclick=()=>selectCamera(row.value)}else if(opt.mount){label=document.createElement('button');label.type='button';label.className='bar-filter-link';label.onclick=()=>selectMount(row.value)}else if(opt.libraryParam){label=document.createElement('a');label.className='bar-link';const f={[opt.libraryParam]:row.value};if(selectedCamera&&opt.includeCamera)f.camera=selectedCamera;label.href=libraryUrl(f)}else label=document.createElement('span');const txt=document.createElement('span');txt.textContent=row.value;label.append(txt);label.title=row.value;if(opt.referenceType)addReferenceHandlers(label,opt.referenceType,row.value);const p=document.createElement('progress');p.max=max;p.value=row.count;const c=document.createElement('strong');c.textContent=opt.total?`${nf.format(row.count)} · ${pct(row.count,opt.total)}%`:nf.format(row.count);item.append(label,p,c);if(opt.camera&&opt.openLibrary!==false){const a=document.createElement('a');a.className='mini-filter';a.href=libraryUrl({camera:row.value});a.textContent='Open Library';item.append(a)}host.append(item)}}
function renderCameraUsage(){const scope=mountDetail();const rows=scope?.cameras||archiveData.cameras||[];syncToggle('showAllCameras','showAllCamerasLabel','cameras',rows.length);renderBars('cameraBars',rowsVisible(rows,'showAllCameras'),{camera:true,total:scope?.total_photos||archiveData.total_photos,referenceType:'camera',openLibrary:!scope})}
function renderLensUsage(){const camera=selectedCamera?archiveData.camera_breakdowns?.[selectedCamera]:null,scope=mountDetail();const rows=camera?.lenses||scope?.lenses||archiveData.lenses||[];syncToggle('showAllLenses','showAllLensesLabel','lenses',rows.length);renderBars('lensBars',rowsVisible(rows,'showAllLenses'),{libraryParam:'lens',includeCamera:!!camera,total:camera?.total_photos||scope?.total_photos||archiveData.total_photos,referenceType:'lens'})}
function coverage(v,t,label){return `${label}: ${nf.format(v||0)} of ${nf.format(t||0)} photos (${pct(v||0,t||0)}%)`}
function renderTechnical(scope=archiveData){renderBars('makerBars',scope.makers||[],{total:scope.total_photos});renderBars('megapixelBars',scope.megapixels||[],{total:scope.megapixel_coverage||scope.total_photos});renderBars('sensorBars',scope.sensor_sizes||[],{total:scope.sensor_coverage||scope.total_photos});renderBars('sensorTypeBars',scope.sensor_types||[],{total:scope.sensor_type_coverage||scope.total_photos});renderBars('mountBars',archiveData.mounts||[],{total:archiveData.mount_coverage||archiveData.total_photos,mount:true});$('makerCoverage').textContent=coverage(scope.maker_coverage,scope.total_photos,'Maker coverage');$('megapixelCoverage').textContent=coverage(scope.megapixel_coverage,scope.total_photos,'Resolution coverage');$('sensorCoverage').textContent=coverage(scope.sensor_coverage,scope.total_photos,'Sensor-size coverage');$('sensorTypeCoverage').textContent=coverage(scope.sensor_type_coverage,scope.total_photos,'Sensor-type coverage');$('mountCoverage').textContent=coverage(archiveData.mount_coverage,archiveData.total_photos,'Lens-mount coverage')}
function fact(l,v){if(v===null||v===undefined||v==='')return null;const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=l;dd.textContent=String(v);return[dt,dd]}
function populateReference(type,name,d){$('referenceType').textContent=type==='camera'?'CAMERA · CATALOG REFERENCE':'LENS · CATALOG REFERENCE';$('referenceTitle').textContent=name;const host=$('referenceFacts');host.replaceChildren();const share=`${nf.format(d.total_photos)} · ${pct(d.total_photos,archiveData.total_photos)}% of archive`;const rows=type==='camera'?[fact('Maker',d.maker||'Not reported'),fact('Usage',share),fact('Resolution',d.megapixels||'Not reported'),fact('Sensor size',d.sensor_size||'Not reported'),fact('Sensor type',d.sensor_type||'Not reported'),fact('Lens mount',top(d.mounts)||'Not reported'),fact('Lenses used',nf.format(d.distinct_lenses||0)),fact('Notes',d.notes)]:[fact('Maker',d.maker||'Not reported'),fact('Usage',share),fact('Lens mount',top(d.mounts)||'Not reported'),fact('Lens type',d.lens_type||'Not reported'),fact('Focal range',d.focal_range),fact('Maximum aperture',d.max_aperture),fact('Cameras used',nf.format(d.distinct_cameras||0)),fact('Notes',d.notes)];for(const r of rows)if(r)host.append(...r);$('referenceFooter').textContent=Object.keys(d.override||{}).length?'Gear database override active. Originals are unchanged.':'Derived from indexed EXIF/inferred catalog metadata.'}
function cancelReferenceHide(){clearTimeout(referenceHideTimer)} function scheduleReferenceHide(){cancelReferenceHide();referenceHideTimer=setTimeout(()=>hideReference(),220)}
function addReferenceHandlers(t,type,name){t.classList.add('has-reference');const m=document.createElement('span');m.className='reference-mark';m.textContent='ⓘ';t.append(m);t.onmouseenter=()=>showReference(t,type,name);t.onmouseleave=scheduleReferenceHide;t.onfocus=()=>showReference(t,type,name);t.onblur=scheduleReferenceHide}
function showReference(t,type,name){if(referenceEditing)return;const d=type==='camera'?archiveData.camera_breakdowns?.[name]:archiveData.lens_breakdowns?.[name];if(!d)return;cancelReferenceHide();referenceState={target:t,type,name,detail:d};populateReference(type,name,d);$('referenceFacts').hidden=false;$('referenceFooter').hidden=false;$('referenceActions').hidden=false;$('referenceEditForm').hidden=true;const c=$('referenceCard');c.hidden=false;positionReference(t)}
function positionReference(t){const c=$('referenceCard'),r=t.getBoundingClientRect();c.style.width=`${Math.min(380,innerWidth-24)}px`;const cr=c.getBoundingClientRect();let l=Math.max(12,Math.min(r.left,innerWidth-cr.width-12)),top=r.bottom+8;if(top+cr.height>innerHeight-12)top=Math.max(12,r.top-cr.height-8);c.style.left=`${l}px`;c.style.top=`${top}px`}
function hideReference(force=false){cancelReferenceHide();if(referenceEditing&&!force)return;referenceEditing=false;referenceState=null;$('referenceCard').hidden=true}
function beginReferenceEdit(){if(!referenceState)return;referenceEditing=true;const d=referenceState.detail;$('referenceFacts').hidden=true;$('referenceFooter').hidden=true;$('referenceActions').hidden=true;$('referenceEditForm').hidden=false;$('referenceMaker').value=d.override?.maker||'';$('referenceMaker').placeholder=d.maker||'Not reported';$('referenceMount').value=d.override?.mount||'';$('referenceMount').placeholder=top(d.mounts)||'Not reported';positionReference(referenceState.target)}
async function saveReference(clear=false){if(!referenceState)return;const{type,name}=referenceState;try{await api('/api/statistics/reference',{method:'PUT',body:JSON.stringify({type,name,maker:clear?'':$('referenceMaker').value.trim(),mount:clear?'':$('referenceMount').value.trim()})});referenceEditing=false;hideReference(true);await load(true)}catch(e){$('referenceEditStatus').textContent=e.message}}
function svg(n,a={}){const x=document.createElementNS('http://www.w3.org/2000/svg',n);for(const[k,v]of Object.entries(a))x.setAttribute(k,v);return x}
function renderTrendRows(rows,names){const chart=$('cameraTrend');chart.replaceChildren();$('trendLegend').replaceChildren();if(!rows?.length||!names?.length){$('trendDates').replaceChildren();return}const L=45,R=980,T=20,B=285,W=R-L,H=B-T;let max=1;for(const row of rows)for(const n of names)max=Math.max(max,row.cameras?.[n]||0);for(let i=0;i<=4;i++){const y=T+H*i/4;chart.append(svg('line',{x1:L,y1:y,x2:R,y2:y,class:'chart-grid'}));const t=svg('text',{x:5,y:y+4,class:'chart-label'});t.textContent=nf.format(Math.round(max*(1-i/4)));chart.append(t)}names.forEach((n,i)=>{const pts=rows.map((row,j)=>`${L+(rows.length===1?W/2:W*j/(rows.length-1))},${B-H*((row.cameras?.[n]||0)/max)}`).join(' ');chart.append(svg('polyline',{points:pts,class:`trend-line line-${i}`}));const b=document.createElement('button');b.className=`legend-item legend-${i}`;b.onclick=()=>selectCamera(n);const s=document.createElement('span');s.className='legend-swatch';const tx=document.createElement('span');tx.textContent=n;b.append(s,tx);$('trendLegend').append(b)});$('trendDates').replaceChildren();const a=document.createElement('span'),z=document.createElement('span');a.textContent=rows[0].month;z.textContent=rows.at(-1).month;$('trendDates').append(a,z)}
function renderSummary(scope,type='global'){if(type==='global'){$('summaryLabel1').textContent='Photographs';$('totalPhotos').textContent=nf.format(scope.total_photos);$('summaryLabel2').textContent='With capture date';$('datedPhotos').textContent=`${nf.format(scope.dated_photos)} (${pct(scope.dated_photos,scope.total_photos)}%)`;$('summaryLabel3').textContent='Cameras';$('distinctCameras').textContent=nf.format(scope.distinct_cameras);$('summaryLabel4').textContent='Lenses';$('distinctLenses').textContent=nf.format(scope.distinct_lenses)}else{$('summaryLabel1').textContent=type==='camera'?'Camera photographs':'Mount photographs';$('totalPhotos').textContent=nf.format(scope.total_photos);$('summaryLabel2').textContent='Archive share';$('datedPhotos').textContent=`${pct(scope.total_photos,archiveData.total_photos)}%`;$('summaryLabel3').textContent=type==='camera'?'With capture date':'Cameras';$('distinctCameras').textContent=type==='camera'?nf.format(scope.dated_photos):nf.format(scope.distinct_cameras);$('summaryLabel4').textContent='Lenses';$('distinctLenses').textContent=nf.format(scope.distinct_lenses)}}
function selectCamera(name){const d=archiveData.camera_breakdowns?.[name];if(!d)return;selectedMount='';$('mountFilter').value='';$('mountScope').hidden=true;selectedCamera=name;$('cameraScope').hidden=false;$('cameraScopeName').textContent=name;$('cameraScopeSummary').textContent=`${nf.format(d.total_photos)} photos · ${nf.format(d.distinct_lenses)} lenses${d.sensor_size?' · '+d.sensor_size:''}`;$('cameraScopeLibrary').href=libraryUrl({camera:name});$('lensHeading').textContent=`Lens usage — ${name}`;$('focalHeading').textContent=`Focal lengths — ${name}`;$('apertureHeading').textContent=`Apertures — ${name}`;$('trendHeading').textContent=`${name} usage — last 60 active months`;$('yearHeading').textContent=`${name} photographs by year`;renderSummary(d,'camera');renderCameraUsage();renderLensUsage();renderBars('focalBars',d.focal_lengths,{total:d.total_photos});renderBars('apertureBars',d.apertures,{total:d.total_photos});renderBars('yearBars',(d.yearly||[]).map(x=>({value:x.year,count:x.count})).reverse(),{total:d.dated_photos});renderTrendRows((d.trend||[]).map(x=>({month:x.month,cameras:{[name]:x.count}})),[name]);renderTechnical(archiveData)}
function selectMount(name){if(!name){clearMount();return}const d=archiveData.mount_breakdowns?.[name];if(!d)return;selectedCamera='';$('cameraScope').hidden=true;selectedMount=name;$('mountFilter').value=name;$('mountScope').hidden=false;$('mountScopeName').textContent=name;$('mountScopeSummary').textContent=`${nf.format(d.total_photos)} photos · ${nf.format(d.distinct_cameras)} cameras · ${nf.format(d.distinct_lenses)} lenses`;$('lensHeading').textContent=`Lens usage — ${name}`;$('focalHeading').textContent=`Focal lengths — ${name}`;$('apertureHeading').textContent=`Apertures — ${name}`;$('trendHeading').textContent=`${name} camera usage — last 60 active months`;$('yearHeading').textContent=`${name} photographs by year`;renderSummary(d,'mount');renderCameraUsage();renderLensUsage();renderBars('focalBars',d.focal_lengths,{total:d.total_photos});renderBars('apertureBars',d.apertures,{total:d.total_photos});renderTechnical(d);renderBars('yearBars',(d.yearly||[]).map(x=>({value:x.year,count:x.count})).reverse(),{total:d.dated_photos});renderTrendRows(d.trend||[],d.top_camera_names||[])}
function resetHeadings(){$('lensHeading').textContent='Lens usage';$('focalHeading').textContent='Most-used focal lengths';$('apertureHeading').textContent='Most-used apertures';$('trendHeading').textContent='Camera usage — last 60 active months';$('yearHeading').textContent='Photographs by year'}
function clearCamera(){selectedCamera='';$('cameraScope').hidden=true;resetHeadings();show(archiveData,false)} function clearMount(){selectedMount='';$('mountFilter').value='';$('mountScope').hidden=true;resetHeadings();show(archiveData,false)}
function populateMounts(){const s=$('mountFilter'),value=selectedMount;s.replaceChildren(new Option('All lens mounts',''));for(const r of archiveData.mounts||[])s.append(new Option(`${r.value} (${nf.format(r.count)})`,r.value));s.value=value}
function show(data,reset=true){archiveData=data;if(reset){selectedCamera='';selectedMount=''}hideReference(true);$('statisticsLoading').hidden=true;$('statisticsContent').hidden=false;$('statisticsError').hidden=true;$('cameraScope').hidden=!selectedCamera;$('mountScope').hidden=!selectedMount;populateMounts();if(selectedMount){selectMount(selectedMount);return}if(selectedCamera){selectCamera(selectedCamera);return}renderSummary(data);renderCameraUsage();renderLensUsage();renderBars('focalBars',data.focal_lengths,{total:data.total_photos});renderBars('apertureBars',data.apertures,{total:data.total_photos});renderTechnical(data);renderBars('yearBars',(data.yearly||[]).map(x=>({value:x.year,count:x.count})).reverse(),{total:data.dated_photos});renderTrendRows(data.trend||[],data.top_camera_names||[]);$('generatedAt').textContent=`${data.cached?'Cached':'Calculated'} ${new Date(data.generated_at).toLocaleString()}`}
async function load(force=false){$('statisticsLoading').hidden=false;$('refreshStats').disabled=true;try{show(await api('/api/statistics'+(force?'?refresh=1':'')),true)}catch(e){$('statisticsLoading').hidden=true;showError(e)}finally{$('refreshStats').disabled=false}}
$('clearCameraScope').onclick=clearCamera;$('clearMountScope').onclick=clearMount;$('mountFilter').onchange=e=>selectMount(e.target.value);$('showAllCameras').onchange=renderCameraUsage;$('showAllLenses').onchange=renderLensUsage;$('refreshStats').onclick=()=>load(true);$('signOut').onclick=async()=>{try{await api('/api/logout',{method:'POST'})}finally{location.href='/'}};$('referenceCard').onmouseenter=cancelReferenceHide;$('referenceCard').onmouseleave=scheduleReferenceHide;$('editReference').onclick=beginReferenceEdit;$('referenceCancel').onclick=()=>{referenceEditing=false;if(referenceState)showReference(referenceState.target,referenceState.type,referenceState.name)};$('referenceClear').onclick=()=>saveReference(true);$('referenceEditForm').onsubmit=e=>{e.preventDefault();saveReference(false)};addEventListener('scroll',()=>hideReference(true),{passive:true});addEventListener('resize',()=>hideReference(true));
(async()=>{try{const s=await api('/api/session');csrf=s.csrf;if(!s.authenticated){location.href='/';return}$('statisticsApp').hidden=false;await load()}catch(e){$('statisticsApp').hidden=false;$('statisticsLoading').hidden=true;showError(e)}})();
''')

# Statistics CSS additions.
with Path('app/static/statistics.css').open('a', encoding='utf-8') as f:
    f.write("\n.stats-filter{display:flex;align-items:center;gap:.8rem;flex-wrap:wrap;background:#191a20;border:1px solid var(--line);border-radius:10px;padding:.85rem 1rem;margin-bottom:1.2rem}.stats-filter label{font-size:.8rem;color:var(--muted)}.stats-filter select{min-width:240px;max-width:420px}.stats-filter span{font-size:.72rem;color:var(--muted)}\n")

# Gear database page.
write('app/static/catalog.html', r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><title>RAW Catalog Gear Database</title><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/controls.css"><link rel="stylesheet" href="/static/catalog.css"><script defer src="/static/catalog.js"></script></head><body><div id="catalogApp" hidden><header><div class="brand"><span class="mark">R / C</span><div><strong>RAW Catalog</strong><span>Gear database</span></div></div><div class="header-actions"><a class="button-link" href="/">Library</a><a class="button-link" href="/statistics">Statistics</a><a class="button-link" href="/settings">Settings</a><button id="refreshCatalog">Refresh facts</button><button id="signOut">Sign out</button></div></header><main class="catalog-main"><div class="title-row"><div><p class="eyebrow">CATALOG DATABASE</p><h1>Cameras and lenses</h1><p class="catalog-intro">Fine-tune technical facts used by statistics. Overrides never modify RAW files or embedded EXIF.</p></div><span id="generatedAt" class="read-only">—</span></div><div id="catalogError" class="error" hidden></div><section class="catalog-toolbar"><div class="tabs"><button id="cameraTab" class="active">Cameras</button><button id="lensTab">Lenses</button></div><input id="catalogSearch" type="search" placeholder="Search cameras or lenses" autocomplete="off"><span id="catalogCount" class="read-only"></span></section><div id="catalogLoading" class="empty"><h2>Loading catalog facts…</h2></div><section id="catalogList" class="catalog-list"></section></main><datalist id="mountOptions"></datalist><footer>RAW Catalog <span>User overrides are stored in the catalog database only</span></footer></div></body></html>''')
write('app/static/catalog.css', r'''.catalog-main{max-width:1450px}.catalog-intro{color:var(--muted);max-width:760px;line-height:1.45}.catalog-toolbar{display:flex;gap:1rem;align-items:center;flex-wrap:wrap;margin:1rem 0}.tabs{display:flex;gap:.5rem}.tabs button.active{border-color:var(--accent);background:#242a1d}.catalog-toolbar input{min-width:280px;flex:1;max-width:560px}.catalog-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem}.gear-card{background:#191a20;border:1px solid var(--line);border-radius:10px;padding:1rem}.gear-card.overridden{border-color:#4a5f2c}.gear-head{display:flex;justify-content:space-between;gap:1rem;margin-bottom:.8rem}.gear-head h2{font-size:1rem;margin:0;overflow-wrap:anywhere}.gear-head span{color:var(--muted);font-size:.75rem;white-space:nowrap}.gear-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.75rem}.gear-field{display:flex;flex-direction:column;gap:.28rem}.gear-field.full{grid-column:1/-1}.gear-field label{font-size:.72rem;color:var(--muted)}.gear-field small{font-size:.66rem;color:var(--muted);line-height:1.35;min-height:1.35em}.gear-field textarea{min-height:72px;resize:vertical}.gear-actions{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin-top:.9rem}.gear-status{font-size:.72rem;color:var(--muted)}@media(max-width:900px){.catalog-list{grid-template-columns:1fr}}@media(max-width:620px){.gear-fields{grid-template-columns:1fr}.gear-field.full{grid-column:auto}.catalog-toolbar input{min-width:100%}}''')
write('app/static/catalog.js', r''''use strict';const $=id=>document.getElementById(id),nf=new Intl.NumberFormat();let csrf='',data=null,kind='camera';async function api(url,options={}){const r=await fetch(url,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...(options.headers||{})}});const j=await r.json();if(!r.ok){if(r.status===401)location.href='/';throw new Error(j.error||`Request failed (${r.status})`)}return j}function err(e){$('catalogError').textContent=e.message;$('catalogError').hidden=false}function derivedText(field,facts){const v=facts?.[field];return v?`Indexed/derived: ${v}`:'No indexed value'}function render(){if(!data)return;const q=$('catalogSearch').value.trim().toLowerCase(),rows=(kind==='camera'?data.cameras:data.lenses).filter(r=>!q||r.name.toLowerCase().includes(q)||Object.values(r.facts||{}).some(v=>String(v||'').toLowerCase().includes(q)));$('catalogCount').textContent=`${nf.format(rows.length)} ${kind}${rows.length===1?'':'s'}`;const host=$('catalogList');host.replaceChildren();for(const row of rows){const card=document.createElement('form');card.className='gear-card'+(Object.keys(row.override||{}).length?' overridden':'');const head=document.createElement('div');head.className='gear-head';const h=document.createElement('h2');h.textContent=row.name;const count=document.createElement('span');count.textContent=`${nf.format(row.count)} photos`;head.append(h,count);card.append(head);const fields=document.createElement('div');fields.className='gear-fields';for(const def of data.fields[kind]){const wrap=document.createElement('div');wrap.className='gear-field'+(def.multiline?' full':'');const label=document.createElement('label');label.textContent=def.label;let input;if(def.multiline){input=document.createElement('textarea')}else{input=document.createElement('input');input.type='text'}input.maxLength=def.max_length||190;input.dataset.field=def.name;input.value=row.override?.[def.name]||'';input.placeholder=row.facts?.[def.name]||'';if(def.name==='mount')input.setAttribute('list','mountOptions');const small=document.createElement('small');small.textContent=derivedText(def.name,row.facts);wrap.append(label,input,small);fields.append(wrap)}card.append(fields);const actions=document.createElement('div');actions.className='gear-actions';const save=document.createElement('button');save.className='primary';save.type='submit';save.textContent='Save override';const clear=document.createElement('button');clear.type='button';clear.textContent='Use indexed values';const status=document.createElement('span');status.className='gear-status';actions.append(save,clear,status);card.append(actions);card.onsubmit=async e=>{e.preventDefault();save.disabled=true;status.textContent='Saving…';const payload={type:kind,name:row.name};card.querySelectorAll('[data-field]').forEach(i=>payload[i.dataset.field]=i.value.trim());try{const res=await api('/api/statistics/reference',{method:'PUT',body:JSON.stringify(payload)});row.override=res.override;card.classList.toggle('overridden',Object.keys(res.override).length>0);status.textContent='Saved. Statistics cache will refresh automatically.'}catch(e){status.textContent=e.message}finally{save.disabled=false}};clear.onclick=async()=>{for(const i of card.querySelectorAll('[data-field]'))i.value='';card.requestSubmit()};host.append(card)}}async function load(refresh=false){$('catalogLoading').hidden=false;$('catalogList').replaceChildren();try{data=await api('/api/reference-database'+(refresh?'?refresh=1':''));$('catalogError').hidden=true;$('generatedAt').textContent=data.generated_at?new Date(data.generated_at).toLocaleString():'—';const dl=$('mountOptions');dl.replaceChildren();for(const m of data.mount_options||[])dl.append(new Option(m));render()}catch(e){err(e)}finally{$('catalogLoading').hidden=true}}function setKind(k){kind=k;$('cameraTab').classList.toggle('active',k==='camera');$('lensTab').classList.toggle('active',k==='lens');render()}$('cameraTab').onclick=()=>setKind('camera');$('lensTab').onclick=()=>setKind('lens');$('catalogSearch').oninput=render;$('refreshCatalog').onclick=()=>load(true);$('signOut').onclick=async()=>{try{await api('/api/logout',{method:'POST'})}finally{location.href='/'}};(async()=>{try{const s=await api('/api/session');csrf=s.csrf;if(!s.authenticated){location.href='/';return}$('catalogApp').hidden=false;await load()}catch(e){$('catalogApp').hidden=false;err(e)}})();''')

# Add navigation entry to Settings.
replace('app/static/settings.html',
'<a class="button-link" href="/statistics">Statistics</a><button id="signOut">Sign out</button>',
'<a class="button-link" href="/statistics">Statistics</a><a class="button-link" href="/catalog">Gear database</a><button id="signOut">Sign out</button>')

# Tests: richer tags, macOS skip helper, reference DB and mount scope.
replace('tests/test_statistics_technical.py',
"                'FocalPlaneYResolution', 'FocalPlaneResolutionUnit'):\n",
"                'FocalPlaneYResolution', 'FocalPlaneResolutionUnit', 'SensorType', 'MaxApertureValue'):\n")
with Path('tests/test_statistics_technical.py').open('a', encoding='utf-8') as f:
    f.write(r'''


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
''')
