import hashlib
import json

from app.db import Setting

REFERENCE_OVERRIDES_KEY = 'catalog_reference_overrides_v1'


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


def _field(value, label):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError(f'{label} must be text')
    value = value.strip()
    if len(value) > 190 or any(ord(char) < 32 for char in value):
        raise ValueError(f'Invalid {label.lower()}')
    return value


def save_reference_override(db, kind, name, maker, mount):
    if kind not in ('camera', 'lens'):
        raise ValueError('Reference type must be camera or lens')
    name = _field(name, 'Name')
    if not name:
        raise ValueError('Reference name is required')
    maker = _field(maker, 'Maker')
    mount = _field(mount, 'Lens mount')
    overrides = load_reference_overrides(db)
    fields = {}
    if maker:
        fields['maker'] = maker
    if mount:
        fields['mount'] = mount
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
