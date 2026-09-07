import fcntl
import hashlib
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from sqlalchemy import select, update, func
from app.db import Session, Photo, Scan, Setting, init_db, now
from app.imaging import EXTENSIONS, metadata_batch, make_previews, make_thumbnail, make_preview, cache_file
from app.storage import (cache_root, configured_thumbnail_folder, configured_preview_folder,
                         resolve_thumbnail_root, resolve_preview_root)

log = logging.getLogger(__name__)
CACHE = os.environ.get('CACHE_DIR', '/data/cache')
ROOT = Path(os.environ.get('PHOTO_ROOT', '/photos')).absolute()
SELECTED_FOLDER_KEY = 'selected_photo_folder'
SCAN_MODE_PREFIX = 'scan_mode:'


def digest(value):
    return hashlib.sha256(value.encode('utf-8', errors='surrogateescape')).hexdigest()


def selected_scan_root():
    with Session() as db:
        setting = db.get(Setting, SELECTED_FOLDER_KEY)
        relative = setting.value if setting else ''
    requested = Path(relative)
    if requested.is_absolute() or '..' in requested.parts:
        raise RuntimeError('Configured photo folder is outside the photo root')
    try:
        base = ROOT.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f'Photo root is unavailable: {ROOT}') from exc
    current = base
    for part in requested.parts:
        if part in ('', '.'):
            continue
        candidate = current / part
        if candidate.is_symlink():
            raise RuntimeError(f'Configured photo folder contains a symbolic link: {candidate}')
        current = candidate
    try:
        target = current.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f'Selected photo folder is unavailable: {current}') from exc
    if target != base and base not in target.parents:
        raise RuntimeError('Configured photo folder is outside the photo root')
    if not target.is_dir():
        raise RuntimeError(f'Selected photo folder is not a directory: {target}')
    return target


def current_thumbnail_root():
    with Session() as db:
        relative = configured_thumbnail_folder(db)
    try:
        return resolve_thumbnail_root(relative, create=True)
    except (ValueError, RuntimeError) as exc:
        raise RuntimeError(f'Invalid thumbnail folder: {exc}') from exc


def current_preview_root():
    with Session() as db:
        relative = configured_preview_folder(db)
    try:
        return resolve_preview_root(relative, create=True)
    except (ValueError, RuntimeError) as exc:
        raise RuntimeError(f'Invalid preview folder: {exc}') from exc


def capture_date(meta):
    for tag in ('DateTimeOriginal', 'CreateDate'):
        try:
            return datetime.strptime(str(meta.get(tag, ''))[:19], '%Y:%m:%d %H:%M:%S')
        except ValueError:
            pass
    return None


def labels(meta):
    make = str(meta.get('Make') or '').strip()
    model = str(meta.get('Model') or '').strip()
    camera = model if model.lower().startswith(make.lower()) else f'{make} {model}'.strip()
    lens = next((str(meta[k]).strip() for k in ('LensModel', 'LensID', 'Lens', 'LensType')
                 if meta.get(k) and str(meta[k]).strip().lower() not in ('unknown', '0')), 'Unknown lens')
    return (camera or 'Unknown camera')[:190], lens[:190]


def scan(job_id):
    counts = dict(discovered=0, indexed=0, skipped=0, errors=0)
    last_message = 'Scanning folders'

    def report(path='', message=None):
        nonlocal last_message
        if message:
            last_message = message
        with Session.begin() as db:
            job = db.get(Scan, job_id)
            if job.cancel:
                raise InterruptedError('Scan cancelled; completed work has been kept')
            for key, value in counts.items():
                setattr(job, key, value)
            job.current_path = str(path)
            job.message = last_message
            job.updated_at = now()

    def walk_error(error):
        counts['errors'] += 1
        report(message=f'Folder could not be read: {error}')

    with Session.begin() as db:
        job = db.get(Scan, job_id)
        force = job.force
        job.state, job.updated_at = 'running', now()

    try:
        thumbnail_cache = current_thumbnail_root()
        preview_cache = current_preview_root()
        legacy_cache = cache_root()

        def process(paths):
            pending = []
            hashes = [digest(str(p)) for p in paths]
            with Session() as db:
                existing = {p.path_hash: p for p in db.scalars(select(Photo).where(Photo.path_hash.in_(hashes)))}
            for path, path_hash in zip(paths, hashes):
                try:
                    stat = path.stat()
                    old = existing.get(path_hash)
                    preview_ok = False
                    thumb_ok = False
                    if old and old.cache_key and not old.preview_error:
                        preview_ok = (cache_file(preview_cache, old.cache_key, 'preview').is_file() or
                                      cache_file(legacy_cache, old.cache_key, 'preview').is_file())
                        thumb_ok = (cache_file(thumbnail_cache, old.cache_key, 'thumb').is_file() or
                                    cache_file(legacy_cache, old.cache_key, 'thumb').is_file())
                    if old and not force and old.size == stat.st_size and old.mtime_ns == stat.st_mtime_ns and preview_ok and thumb_ok:
                        counts['skipped'] += 1
                    else:
                        pending.append((path, path_hash, stat))
                except OSError as exc:
                    counts['errors'] += 1
                    report(path, str(exc))
            report()
            if not pending:
                return
            try:
                metadata = metadata_batch([p for p, _, _ in pending])
            except Exception as exc:
                counts['errors'] += len(pending)
                report(message=f'Metadata batch failed: {exc}')
                return
            for path, path_hash, stat in pending:
                report(path)
                try:
                    meta = metadata.get(str(path))
                    if not meta or meta.get('Error'):
                        raise ValueError((meta or {}).get('Error', 'No metadata returned'))
                    camera, lens = labels(meta)
                    key = digest(f'{path_hash}:{stat.st_mtime_ns}:{stat.st_size}')
                    preview_error = None
                    try:
                        make_previews(path, meta, preview_cache, key, thumbnail_cache)
                    except Exception as exc:
                        preview_error = str(exc)[:2000]
                        counts['errors'] += 1
                        log.warning('Preview failed for %s: %s', path, exc)
                    after = path.stat()
                    if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError('File changed during indexing; retry next scan')
                    with Session.begin() as db:
                        photo = db.scalar(select(Photo).where(Photo.path_hash == path_hash))
                        if photo is None:
                            photo = Photo(path_hash=path_hash)
                            db.add(photo)
                        photo.path, photo.filename = str(path), path.name
                        photo.size, photo.mtime_ns = stat.st_size, stat.st_mtime_ns
                        photo.camera, photo.lens = camera, lens
                        photo.taken_at, photo.metadata_json = capture_date(meta), meta
                        photo.cache_key = key if not preview_error else None
                        photo.preview_error, photo.indexed_at = preview_error, now()
                    counts['indexed'] += 1
                except Exception as exc:
                    counts['errors'] += 1
                    report(path, f'Could not index file: {exc}')
                    log.exception('Could not index %s', path)
            report()

        root = selected_scan_root()
        pending = []
        for directory, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
            directories[:] = [d for d in directories if not Path(directory, d).is_symlink()]
            report(directory)
            for filename in filenames:
                path = Path(directory, filename)
                if path.suffix.lower() not in EXTENSIONS or path.is_symlink():
                    continue
                counts['discovered'] += 1
                pending.append(path)
                if len(pending) >= 64:
                    process(pending)
                    pending = []
        if pending:
            process(pending)
        report(message='Scan complete' if not counts['errors'] else 'Scan complete with errors; see worker logs')
        state, message = 'done', last_message
    except InterruptedError as exc:
        state, message = 'cancelled', str(exc)
    except Exception as exc:
        state, message = 'failed', str(exc)
        log.exception('Scan failed')
    with Session.begin() as db:
        job = db.get(Scan, job_id)
        for key, value in counts.items():
            setattr(job, key, value)
        job.state, job.message, job.updated_at = state, message, now()
        job.current_path = ''


def rebuild_media(job_id, kind):
    noun = 'thumbnail' if kind == 'thumb' else 'preview'
    plural = 'thumbnails' if kind == 'thumb' else 'previews'
    counts = dict(discovered=0, indexed=0, skipped=0, errors=0)
    last_message = f'Rebuilding {plural}'

    def report(path='', message=None):
        nonlocal last_message
        if message:
            last_message = message
        with Session.begin() as db:
            job = db.get(Scan, job_id)
            if job.cancel:
                raise InterruptedError(f'{noun.capitalize()} rebuild cancelled; completed {plural} have been kept')
            for key, value in counts.items():
                setattr(job, key, value)
            job.current_path = str(path)
            job.message = last_message
            job.updated_at = now()

    with Session.begin() as db:
        job = db.get(Scan, job_id)
        job.state, job.message, job.updated_at = 'running', last_message, now()
    try:
        output_root = current_thumbnail_root() if kind == 'thumb' else current_preview_root()
        with Session() as db:
            counts['discovered'] = db.scalar(select(func.count()).select_from(Photo).where(Photo.cache_key.is_not(None))) or 0
        report(message=f'Rebuilding {counts["discovered"]} {plural}')
        last_id = 0
        while True:
            with Session() as db:
                batch = list(db.scalars(select(Photo).where(Photo.id > last_id, Photo.cache_key.is_not(None))
                                        .order_by(Photo.id).limit(100)))
            if not batch:
                break
            for photo in batch:
                last_id = photo.id
                report(photo.path)
                try:
                    source = Path(photo.path)
                    if source.is_symlink() or not source.is_file():
                        raise OSError('Original file is unavailable')
                    if kind == 'thumb':
                        make_thumbnail(source, photo.metadata_json or {}, output_root, photo.cache_key)
                    else:
                        make_preview(source, photo.metadata_json or {}, output_root, photo.cache_key)
                    counts['indexed'] += 1
                except Exception as exc:
                    counts['errors'] += 1
                    report(photo.path, f'Could not rebuild {noun}: {exc}')
                    log.warning('%s rebuild failed for %s: %s', noun.capitalize(), photo.path, exc)
        report(message=f'{noun.capitalize()} rebuild complete' if not counts['errors'] else f'{noun.capitalize()} rebuild complete with errors')
        state, message = 'done', last_message
    except InterruptedError as exc:
        state, message = 'cancelled', str(exc)
    except Exception as exc:
        state, message = 'failed', str(exc)
        log.exception('%s rebuild failed', noun.capitalize())
    with Session.begin() as db:
        job = db.get(Scan, job_id)
        for key, value in counts.items():
            setattr(job, key, value)
        job.state, job.message, job.updated_at = state, message, now()
        job.current_path = ''


def rebuild_thumbnails(job_id):
    rebuild_media(job_id, 'thumb')


def rebuild_previews(job_id):
    rebuild_media(job_id, 'preview')


def job_mode(job_id):
    with Session() as db:
        setting = db.get(Setting, SCAN_MODE_PREFIX + str(job_id))
        return setting.value if setting else 'scan'


def clear_job_mode(job_id):
    with Session.begin() as db:
        setting = db.get(Setting, SCAN_MODE_PREFIX + str(job_id))
        if setting:
            db.delete(setting)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    Path(CACHE).mkdir(parents=True, exist_ok=True)
    # One worker across containers sharing this cache, including CLI invocations.
    lock = open(Path(CACHE) / 'worker.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    init_db()
    with Session.begin() as db:
        db.execute(update(Scan).where(Scan.state == 'running').values(state='failed', message='Indexer restarted; start a new scan to resume incrementally', updated_at=now()))
    while True:
        try:
            with Session() as db:
                job = db.scalar(select(Scan).where(Scan.state == 'queued').order_by(Scan.id).limit(1))
            if job:
                mode = job_mode(job.id)
                if mode == 'thumbnails':
                    rebuild_thumbnails(job.id)
                elif mode == 'previews':
                    rebuild_previews(job.id)
                else:
                    scan(job.id)
                clear_job_mode(job.id)
            else:
                time.sleep(2)
        except Exception:
            log.exception('Worker loop failed; retrying')
            time.sleep(5)


if __name__ == '__main__':
    main()
