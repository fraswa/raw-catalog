import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from sqlalchemy import select, func

from app.db import Photo, Setting
from app.reference import load_reference_overrides, reference_fingerprint

CACHE_KEY = 'statistics_cache_v4'
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

    # Some cameras expose focal-plane pixel density rather than sensor dimensions.
    # Convert the active pixel dimensions back to millimetres when the units are known.
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

    # Conservative model-name fallbacks improve coverage for common native lenses while
    # leaving ambiguous adapted/manual lenses unclassified.
    text = str(lens or '').strip().upper()
    maker = str(metadata.get('Make') or '').strip().upper()
    if text.startswith('RF-S') or text.startswith('RF '):
        return 'Canon RF'
    if text.startswith('EF-S'):
        return 'Canon EF-S'
    if text.startswith('EF-M'):
        return 'Canon EF-M'
    if text.startswith('EF '):
        return 'Canon EF'
    if 'NIKKOR Z' in text:
        return 'Nikon Z'
    if maker.startswith('NIKON') and 'NIKKOR' in text:
        return 'Nikon F'
    if maker.startswith('SONY') and (text.startswith('FE ') or text.startswith('E ') or text.startswith('E PZ')):
        return 'Sony E'
    if maker.startswith('FUJIFILM') and (text.startswith('XF') or text.startswith('XC')):
        return 'Fujifilm X'
    if maker.startswith('FUJIFILM') and text.startswith('GF'):
        return 'Fujifilm G'
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
    makers = Counter(); lens_makers = Counter(); mounts = Counter(); sensor_sizes = Counter(); megapixels = Counter()
    camera_months = defaultdict(Counter); lens_months = defaultdict(Counter)
    camera_lenses = defaultdict(Counter); camera_focals = defaultdict(Counter)
    camera_apertures = defaultdict(Counter); camera_years = defaultdict(Counter)
    camera_makers = defaultdict(Counter); camera_mounts = defaultdict(Counter)
    camera_sensors = defaultdict(Counter); camera_megapixels = defaultdict(Counter)
    lens_cameras = defaultdict(Counter); lens_years = defaultdict(Counter)
    lens_focals = defaultdict(Counter); lens_apertures = defaultdict(Counter)
    lens_mounts = defaultdict(Counter); lens_maker_counts = defaultdict(Counter)
    camera_dated = Counter(); lens_dated = Counter()
    dated = total = 0
    overrides = load_reference_overrides(db)
    camera_overrides = overrides.get('camera', {})
    lens_overrides = overrides.get('lens', {})

    statement = select(Photo.camera, Photo.lens, Photo.taken_at, Photo.metadata_json).execution_options(yield_per=2000)
    for camera, lens, taken_at, metadata in db.execute(statement):
        total += 1
        camera = camera or 'Unknown camera'; lens = lens or 'Unknown lens'
        cameras[camera] += 1; lenses[lens] += 1; camera_lenses[camera][lens] += 1; lens_cameras[lens][camera] += 1
        metadata = metadata or {}

        camera_override = camera_overrides.get(camera, {})
        lens_override = lens_overrides.get(lens, {})
        maker = _clean_text(camera_override.get('maker')) or _clean_text(metadata.get('Make'))
        if maker:
            makers[maker] += 1; camera_makers[camera][maker] += 1
        lens_maker = _clean_text(lens_override.get('maker')) or _clean_text(metadata.get('LensMake'))
        if lens_maker:
            lens_makers[lens_maker] += 1; lens_maker_counts[lens][lens_maker] += 1

        megapixel = _megapixels(metadata)
        if megapixel:
            megapixels[megapixel] += 1; camera_megapixels[camera][megapixel] += 1
        sensor = _sensor_size(metadata)
        if sensor:
            sensor_sizes[sensor] += 1; camera_sensors[camera][sensor] += 1
        mount = (_clean_text(lens_override.get('mount')) or _clean_text(camera_override.get('mount'))
                 or _lens_mount(metadata, camera, lens))
        if mount:
            mounts[mount] += 1; camera_mounts[camera][mount] += 1; lens_mounts[lens][mount] += 1

        focal = _number_value(metadata.get('FocalLength')); aperture = _number_value(metadata.get('FNumber'))
        if focal is not None and focal > 0:
            focal_label = _label_number(focal, 1) + ' mm'
            focals[focal_label] += 1; camera_focals[camera][focal_label] += 1; lens_focals[lens][focal_label] += 1
        if aperture is not None and aperture > 0:
            aperture_label = 'f/' + _label_number(aperture, 1)
            apertures[aperture_label] += 1; camera_apertures[camera][aperture_label] += 1; lens_apertures[lens][aperture_label] += 1
        if taken_at:
            dated += 1; camera_dated[camera] += 1; lens_dated[lens] += 1
            month = taken_at.strftime('%Y-%m'); year = taken_at.strftime('%Y')
            months[month] += 1; camera_years[camera][year] += 1; lens_years[lens][year] += 1
            camera_months[month][camera] += 1; lens_months[month][lens] += 1

    month_keys = sorted(months)[-60:]
    top_cameras = [name for name, _ in cameras.most_common(5)]
    top_lenses = [name for name, _ in lenses.most_common(5)]
    trend = [{'month': month, 'total': months[month],
              'cameras': {name: camera_months[month].get(name, 0) for name in top_cameras},
              'lenses': {name: lens_months[month].get(name, 0) for name in top_lenses}}
             for month in month_keys]
    yearly = Counter()
    for month, count in months.items():
        yearly[month[:4]] += count

    camera_breakdowns = {}
    for camera in cameras:
        override = camera_overrides.get(camera, {})
        camera_breakdowns[camera] = {
            'total_photos': cameras[camera],
            'dated_photos': camera_dated[camera],
            'distinct_lenses': len(camera_lenses[camera]),
            'maker': _first(camera_makers[camera]),
            'override_maker': override.get('maker'),
            'override_mount': override.get('mount'),
            'megapixels': _first(camera_megapixels[camera]),
            'sensor_size': _first(camera_sensors[camera]),
            'mounts': _top(camera_mounts[camera], 5),
            'active_years': _active_years(camera_years[camera]),
            'lenses': _top(camera_lenses[camera], None),
            'focal_lengths': _top(camera_focals[camera]),
            'apertures': _top(camera_apertures[camera]),
            'yearly': [{'year': year, 'count': camera_years[camera][year]}
                       for year in sorted(camera_years[camera])],
            'trend': [{'month': month, 'count': camera_months[month].get(camera, 0)}
                      for month in month_keys],
        }

    lens_breakdowns = {}
    for lens in lenses:
        override = lens_overrides.get(lens, {})
        lens_breakdowns[lens] = {
            'total_photos': lenses[lens],
            'dated_photos': lens_dated[lens],
            'distinct_cameras': len(lens_cameras[lens]),
            'maker': _first(lens_maker_counts[lens]),
            'override_maker': override.get('maker'),
            'override_mount': override.get('mount'),
            'mounts': _top(lens_mounts[lens], 5),
            'active_years': _active_years(lens_years[lens]),
            'cameras': _top(lens_cameras[lens]),
            'focal_lengths': _top(lens_focals[lens]),
            'apertures': _top(lens_apertures[lens]),
            'yearly': [{'year': year, 'count': lens_years[lens][year]}
                       for year in sorted(lens_years[lens])],
        }

    payload = {
        'signature': signature, 'cached': False, 'generated_at': datetime.utcnow().isoformat() + 'Z',
        'total_photos': total, 'dated_photos': dated, 'undated_photos': total - dated,
        'distinct_cameras': len(cameras), 'distinct_lenses': len(lenses),
        'distinct_focal_lengths': len(focals), 'distinct_apertures': len(apertures),
        'cameras': _top(cameras, None), 'lenses': _top(lenses, None), 'focal_lengths': _top(focals), 'apertures': _top(apertures),
        'makers': _top(makers), 'lens_makers': _top(lens_makers), 'mounts': _top(mounts),
        'sensor_sizes': _top(sensor_sizes), 'megapixels': _top(megapixels),
        'maker_coverage': sum(makers.values()), 'mount_coverage': sum(mounts.values()),
        'sensor_coverage': sum(sensor_sizes.values()), 'megapixel_coverage': sum(megapixels.values()),
        'top_camera_names': top_cameras, 'top_lens_names': top_lenses, 'trend': trend,
        'yearly': [{'year': year, 'count': yearly[year]} for year in sorted(yearly)],
        'camera_breakdowns': camera_breakdowns, 'lens_breakdowns': lens_breakdowns,
    }

    encoded = json.dumps(payload, separators=(',', ':'))
    setting = db.get(Setting, CACHE_KEY)
    if setting is None:
        db.add(Setting(key=CACHE_KEY, value=encoded))
    else:
        setting.value = encoded
    db.flush()
    return payload
