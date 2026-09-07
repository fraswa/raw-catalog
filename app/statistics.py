import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from sqlalchemy import select, func

from app.db import Photo, Setting

CACHE_KEY = 'statistics_cache_v1'
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


def _label_number(value, digits=1):
    if value is None:
        return None
    rounded = round(value, digits)
    return str(int(rounded)) if float(rounded).is_integer() else f'{rounded:g}'


def _top(counter, limit=20):
    return [{'value': value, 'count': count} for value, count in counter.most_common(limit)]


def _signature(db):
    count, latest = db.execute(select(func.count(Photo.id), func.max(Photo.indexed_at))).one()
    return f'{count}:{latest.isoformat() if latest else ""}'


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
    camera_months = defaultdict(Counter); lens_months = defaultdict(Counter)
    dated = total = 0

    statement = select(Photo.camera, Photo.lens, Photo.taken_at, Photo.metadata_json).execution_options(yield_per=2000)
    for camera, lens, taken_at, metadata in db.execute(statement):
        total += 1
        camera = camera or 'Unknown camera'; lens = lens or 'Unknown lens'
        cameras[camera] += 1; lenses[lens] += 1
        metadata = metadata or {}
        focal = _number_value(metadata.get('FocalLength')); aperture = _number_value(metadata.get('FNumber'))
        if focal is not None and focal > 0: focals[_label_number(focal, 1) + ' mm'] += 1
        if aperture is not None and aperture > 0: apertures['f/' + _label_number(aperture, 1)] += 1
        if taken_at:
            dated += 1
            month = taken_at.strftime('%Y-%m'); months[month] += 1
            camera_months[month][camera] += 1; lens_months[month][lens] += 1

    month_keys = sorted(months)[-60:]
    top_cameras = [name for name, _ in cameras.most_common(5)]
    top_lenses = [name for name, _ in lenses.most_common(5)]
    trend = [{'month': month, 'total': months[month],
              'cameras': {name: camera_months[month].get(name, 0) for name in top_cameras},
              'lenses': {name: lens_months[month].get(name, 0) for name in top_lenses}}
             for month in month_keys]
    yearly = Counter()
    for month, count in months.items(): yearly[month[:4]] += count

    payload = {
        'signature': signature, 'cached': False, 'generated_at': datetime.utcnow().isoformat() + 'Z',
        'total_photos': total, 'dated_photos': dated, 'undated_photos': total - dated,
        'distinct_cameras': len(cameras), 'distinct_lenses': len(lenses),
        'distinct_focal_lengths': len(focals), 'distinct_apertures': len(apertures),
        'cameras': _top(cameras), 'lenses': _top(lenses), 'focal_lengths': _top(focals), 'apertures': _top(apertures),
        'top_camera_names': top_cameras, 'top_lens_names': top_lenses, 'trend': trend,
        'yearly': [{'year': year, 'count': yearly[year]} for year in sorted(yearly)],
    }

    encoded = json.dumps(payload, separators=(',', ':'))
    setting = db.get(Setting, CACHE_KEY)
    if setting is None: db.add(Setting(key=CACHE_KEY, value=encoded))
    else: setting.value = encoded
    db.flush()
    return payload
