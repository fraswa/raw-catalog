import fcntl
import hashlib
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from sqlalchemy import select, update
from app.db import Session, Photo, Scan, init_db, now
from app.imaging import EXTENSIONS, metadata_batch, make_previews, cache_file

log = logging.getLogger(__name__)
CACHE = os.environ.get('CACHE_DIR', '/data/cache')
ROOT = Path(os.environ.get('PHOTO_ROOT', '/photos')).absolute()


def digest(value):
    return hashlib.sha256(value.encode('utf-8', errors='surrogateescape')).hexdigest()


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

    def process(paths):
        pending = []
        hashes = [digest(str(p)) for p in paths]
        with Session() as db:
            existing = {p.path_hash: p for p in db.scalars(select(Photo).where(Photo.path_hash.in_(hashes)))}
        for path, path_hash in zip(paths, hashes):
            try:
                stat = path.stat()
                old = existing.get(path_hash)
                if old and not force and old.size == stat.st_size and old.mtime_ns == stat.st_mtime_ns and old.cache_key and not old.preview_error and all(cache_file(CACHE, old.cache_key, k).is_file() for k in ('thumb', 'preview')):
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
                    make_previews(path, meta, CACHE, key)
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

    with Session.begin() as db:
        job = db.get(Scan, job_id)
        force = job.force
        job.state, job.updated_at = 'running', now()
    try:
        if not ROOT.is_dir() or ROOT.is_symlink():
            raise RuntimeError(f'Photo root is unavailable or is a symlink: {ROOT}')
        pending = []
        for directory, directories, filenames in os.walk(ROOT, followlinks=False, onerror=walk_error):
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
                scan(job.id)
            else:
                time.sleep(2)
        except Exception:
            log.exception('Worker loop failed; retrying')
            time.sleep(5)


if __name__ == '__main__':
    main()
