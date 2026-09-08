import hashlib
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
