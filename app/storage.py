import os
from pathlib import Path

from app.db import Setting

THUMB_FOLDER_KEY = 'thumbnail_folder'
PREVIEW_FOLDER_KEY = 'preview_folder'
DEFAULT_THUMB_FOLDER = 'thumbnails'
DEFAULT_PREVIEW_FOLDER = 'previews'


def cache_root():
    return Path(os.environ.get('CACHE_DIR', '/data/cache'))


def _normalize_folder(value, default, label):
    if value is None or value == '':
        value = default
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError(f'Invalid {label} folder')
    requested = Path(value)
    if requested.is_absolute() or '..' in requested.parts:
        raise ValueError(f'{label.capitalize()} folder must be inside the app cache')
    parts = [part for part in requested.parts if part not in ('', '.')]
    if not parts:
        raise ValueError(f'{label.capitalize()} folder cannot be the cache root')
    return Path(*parts).as_posix()


def normalize_thumbnail_folder(value):
    return _normalize_folder(value, DEFAULT_THUMB_FOLDER, 'thumbnail')


def normalize_preview_folder(value):
    return _normalize_folder(value, DEFAULT_PREVIEW_FOLDER, 'preview')


def configured_thumbnail_folder(db):
    setting = db.get(Setting, THUMB_FOLDER_KEY)
    return normalize_thumbnail_folder(setting.value if setting else DEFAULT_THUMB_FOLDER)


def configured_preview_folder(db):
    setting = db.get(Setting, PREVIEW_FOLDER_KEY)
    return normalize_preview_folder(setting.value if setting else DEFAULT_PREVIEW_FOLDER)


def _resolve_root(relative, normalize, label, create=False):
    relative = normalize(relative)
    base = cache_root()
    try:
        base.mkdir(parents=True, exist_ok=True)
        base_resolved = base.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f'App cache is unavailable: {base}') from exc

    current = base_resolved
    for part in Path(relative).parts:
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

    if target != base_resolved and base_resolved not in target.parents:
        raise ValueError(f'{label.capitalize()} folder must be inside the app cache')
    if create and not target.is_dir():
        raise ValueError(f'{label.capitalize()} path is not a folder')
    return target


def resolve_thumbnail_root(relative, create=False):
    return _resolve_root(relative, normalize_thumbnail_folder, 'thumbnail', create=create)


def resolve_preview_root(relative, create=False):
    return _resolve_root(relative, normalize_preview_folder, 'preview', create=create)
