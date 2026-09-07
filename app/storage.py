import os
from pathlib import Path

from app.db import Setting

THUMB_FOLDER_KEY = 'thumbnail_folder'
PREVIEW_FOLDER_KEY = 'preview_folder'
PREVIEW_EDGE_KEY = 'preview_edge'
DEFAULT_THUMB_FOLDER = 'thumbnails'
DEFAULT_PREVIEW_FOLDER = 'previews'
PREVIEW_EDGES = (1280, 1920, 2560, 3840, 5120)


def cache_root():
    return Path(os.environ.get('CACHE_DIR', '/data/cache'))


def external_storage_root():
    return Path(os.environ.get('EXTERNAL_STORAGE_ROOT', '/storage'))


def storage_roots():
    return (cache_root(), external_storage_root())


def _inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _normalize_folder(value, default, label):
    if value is None or value == '':
        value = str(cache_root() / default)
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError(f'Invalid {label} folder')
    requested = Path(value).expanduser()
    if '..' in requested.parts:
        raise ValueError(f'{label.capitalize()} folder cannot contain ..')
    if not requested.is_absolute():
        requested = cache_root() / requested
    requested = Path(os.path.normpath(str(requested)))
    allowed = [Path(os.path.normpath(str(root))) for root in storage_roots()]
    if not any(_inside(requested, root) for root in allowed):
        raise ValueError(f'{label.capitalize()} folder must be under /data/cache or /storage')
    if requested in allowed:
        raise ValueError(f'{label.capitalize()} folder cannot be a storage root')
    return str(requested)


def normalize_thumbnail_folder(value):
    return _normalize_folder(value, DEFAULT_THUMB_FOLDER, 'thumbnail')


def normalize_preview_folder(value):
    return _normalize_folder(value, DEFAULT_PREVIEW_FOLDER, 'preview')


def configured_thumbnail_folder(db):
    setting = db.get(Setting, THUMB_FOLDER_KEY)
    return normalize_thumbnail_folder(setting.value if setting else None)


def configured_preview_folder(db):
    setting = db.get(Setting, PREVIEW_FOLDER_KEY)
    return normalize_preview_folder(setting.value if setting else None)


def configured_preview_edge(db):
    setting = db.get(Setting, PREVIEW_EDGE_KEY)
    raw = setting.value if setting else os.environ.get('PREVIEW_EDGE', '2560')
    try:
        edge = int(raw)
    except (TypeError, ValueError):
        edge = 2560
    return edge if edge in PREVIEW_EDGES else 2560


def normalize_preview_edge(value):
    try:
        edge = int(value)
    except (TypeError, ValueError):
        raise ValueError('Invalid preview size')
    if edge not in PREVIEW_EDGES:
        raise ValueError('Preview size must be one of: ' + ', '.join(map(str, PREVIEW_EDGES)))
    return edge


def _resolve_root(value, normalize, label, create=False):
    configured = Path(normalize(value))
    allowed = []
    for root in storage_roots():
        try:
            root.mkdir(parents=True, exist_ok=True)
            allowed.append(root.resolve(strict=True))
        except OSError as exc:
            if configured == root or _inside(configured, root):
                raise RuntimeError(f'Storage root is unavailable: {root}') from exc
    if not allowed:
        raise RuntimeError('No writable storage roots are available')

    base = next((root for root in allowed if configured == root or _inside(configured, root)), None)
    if base is None:
        raise ValueError(f'{label.capitalize()} folder is outside allowed storage')

    relative = configured.relative_to(base)
    current = base
    for part in relative.parts:
        candidate = current / part
        if candidate.exists() and candidate.is_symlink():
            raise ValueError(f'{label.capitalize()} folder cannot contain symbolic links')
        current = candidate

    try:
        if create:
            current.mkdir(parents=True, exist_ok=True)
        target = current.resolve(strict=create)
    except FileNotFoundError:
        target = current.resolve(strict=False)
    except OSError as exc:
        raise RuntimeError(f'{label.capitalize()} folder is unavailable: {current}') from exc

    if not _inside(target, base):
        raise ValueError(f'{label.capitalize()} folder is outside allowed storage')
    if create and not target.is_dir():
        raise ValueError(f'{label.capitalize()} path is not a folder')
    return target


def resolve_thumbnail_root(value, create=False):
    return _resolve_root(value, normalize_thumbnail_folder, 'thumbnail', create=create)


def resolve_preview_root(value, create=False):
    return _resolve_root(value, normalize_preview_folder, 'preview', create=create)
