import json
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
