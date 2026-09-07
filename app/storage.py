import os
from pathlib import Path

from app.db import Setting

THUMB_FOLDER_KEY = 'thumbnail_folder'
DEFAULT_THUMB_FOLDER = 'thumbnails'


def cache_root():
    return Path(os.environ.get('CACHE_DIR', '/data/cache'))


def normalize_thumbnail_folder(value):
    if value is None or value == '':
        value = DEFAULT_THUMB_FOLDER
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError('Invalid thumbnail folder')
    requested = Path(value)
    if requested.is_absolute() or '..' in requested.parts:
        raise ValueError('Thumbnail folder must be inside the app cache')
    parts = [part for part in requested.parts if part not in ('', '.')]
    if not parts:
        raise ValueError('Thumbnail folder cannot be the cache root')
    return Path(*parts).as_posix()


def configured_thumbnail_folder(db):
    setting = db.get(Setting, THUMB_FOLDER_KEY)
    return normalize_thumbnail_folder(setting.value if setting else DEFAULT_THUMB_FOLDER)


def resolve_thumbnail_root(relative, create=False):
    relative = normalize_thumbnail_folder(relative)
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
            raise ValueError('Thumbnail folder cannot contain symbolic links')
        current = candidate

    try:
        if create:
            current.mkdir(parents=True, exist_ok=True)
        target = current.resolve(strict=create)
    except FileNotFoundError:
        target = current.resolve(strict=False)
    except OSError as exc:
        raise RuntimeError(f'Thumbnail folder is unavailable: {current}') from exc

    if target != base_resolved and base_resolved not in target.parents:
        raise ValueError('Thumbnail folder must be inside the app cache')
    if create and not target.is_dir():
        raise ValueError('Thumbnail path is not a folder')
    return target
