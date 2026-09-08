import fcntl
import hashlib
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from sqlalchemy import select, update, func, delete, or_
from app.db import Session, Photo, Scan, Setting, init_db, now
from app.imaging import (EXTENSIONS, metadata_batch, make_previews, make_thumbnail, make_preview,
                         cache_file, close_exiftool_sessions)
from app.storage import (cache_root, configured_thumbnail_folder, configured_preview_folder,
                         configured_preview_edge, configured_preview_quality,
                         resolve_thumbnail_root, resolve_preview_root)

log = logging.getLogger(__name__)
CACHE = os.environ.get('CACHE_DIR', '/data/cache')
ROOT = Path(os.environ.get('PHOTO_ROOT', '/photos')).absolute()
SELECTED_FOLDER_KEY = 'selected_photo_folder'
SCAN_MODE_PREFIX = 'scan_mode:'
SCAN_SKIP_PREVIEWS_PREFIX = 'scan_skip_previews:'
SCAN_SKIP_IMPORTED_PREFIX = 'scan_skip_imported:'
SCAN_SKIP_POST_STAT_PREFIX = 'scan_skip_post_stat:'
SCAN_PARALLELISM_PREFIX = 'scan_parallelism:'
PARALLELISM_VALUES = (1, 2, 4, 6, 8)
SCAN_BATCH_SIZE = 128
DB_WRITE_BATCH_SIZE = 16
PROGRESS_INTERVAL = 0.5
MACOS_GARBAGE_DIRS = {'.AppleDouble', '__MACOSX', '.Spotlight-V100', '.Trashes', '.fseventsd'}


def is_macos_garbage_name(name):
    return name == '.DS_Store' or name.startswith('._')


def purge_macos_garbage_rows():
    clauses = [Photo.filename.startswith('._', autoescape=True), Photo.filename == '.DS_Store']
    for directory in ('.AppleDouble', '__MACOSX', '.Spotlight-V100', '.Trashes', '.fseventsd'):
        clauses.append(Photo.path.contains('/' + directory + '/', autoescape=True))
    with Session.begin() as db:
        result = db.execute(delete(Photo).where(or_(*clauses)))
        removed = result.rowcount or 0
    if removed:
        log.info('Removed %s previously indexed macOS metadata rows', removed)
    return removed


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
        value = configured_thumbnail_folder(db)
    try:
        return resolve_thumbnail_root(value, create=True)
    except (ValueError, RuntimeError) as exc:
        raise RuntimeError(f'Invalid thumbnail folder: {exc}') from exc


def current_preview_root():
    with Session() as db:
        value = configured_preview_folder(db)
    try:
        return resolve_preview_root(value, create=True)
    except (ValueError, RuntimeError) as exc:
        raise RuntimeError(f'Invalid preview folder: {exc}') from exc


def current_preview_settings():
    with Session() as db:
        return configured_preview_edge(db), configured_preview_quality(db)


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


def job_skip_previews(job_id):
    with Session() as db:
        setting = db.get(Setting, SCAN_SKIP_PREVIEWS_PREFIX + str(job_id))
        return bool(setting and setting.value == '1')


def job_skip_imported(job_id):
    with Session() as db:
        setting = db.get(Setting, SCAN_SKIP_IMPORTED_PREFIX + str(job_id))
        return bool(setting and setting.value == '1')


def job_skip_post_stat(job_id):
    with Session() as db:
        setting = db.get(Setting, SCAN_SKIP_POST_STAT_PREFIX + str(job_id))
        return bool(setting and setting.value == '1')


def job_parallelism(job_id):
    with Session() as db:
        setting = db.get(Setting, SCAN_PARALLELISM_PREFIX + str(job_id))
    try:
        value = int(setting.value) if setting else 1
    except (TypeError, ValueError):
        return 1
    return value if value in PARALLELISM_VALUES else 1


def scan(job_id):
    counts = dict(discovered=0, indexed=0, skipped=0, errors=0)
    last_message = 'Scanning folders'
    last_report_at = 0.0

    def report(path='', message=None, force=False):
        nonlocal last_message, last_report_at
        if message:
            last_message = message
        current = time.monotonic()
        if not force and current - last_report_at < PROGRESS_INTERVAL:
            return
        with Session.begin() as db:
            job = db.get(Scan, job_id)
            if job.cancel:
                raise InterruptedError('Scan cancelled; completed work has been kept')
            for key, value in counts.items():
                setattr(job, key, value)
            job.current_path = str(path)
            job.message = last_message
            job.updated_at = now()
        last_report_at = current

    def walk_error(error):
        counts['errors'] += 1
        report(message=f'Folder could not be read: {error}', force=True)

    with Session.begin() as db:
        job = db.get(Scan, job_id)
        force = job.force
        job.state, job.updated_at = 'running', now()
    skip_previews = job_skip_previews(job_id)
    skip_imported = job_skip_imported(job_id)
    skip_post_stat = job_skip_post_stat(job_id)
    parallelism = job_parallelism(job_id)
    purge_macos_garbage_rows()

    try:
        thumbnail_cache = current_thumbnail_root()
        preview_cache = None if skip_previews else current_preview_root()
        preview_edge, preview_quality = current_preview_settings()
        legacy_cache = cache_root()

        def render_photo(item):
            path, path_hash, stat, meta = item
            if not meta or meta.get('Error'):
                raise ValueError((meta or {}).get('Error', 'No metadata returned'))
            camera, lens = labels(meta)
            key = digest(f'{path_hash}:{stat.st_mtime_ns}:{stat.st_size}')
            media_error = None
            try:
                if skip_previews:
                    make_thumbnail(path, meta, thumbnail_cache, key)
                else:
                    make_previews(path, meta, preview_cache, key, thumbnail_cache,
                                  preview_edge, preview_quality)
            except Exception as exc:
                media_error = str(exc)[:2000]
                log.warning('Generated media failed for %s: %s', path, exc)
            if not skip_post_stat:
                after = path.stat()
                if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError('File changed during indexing; retry next scan')
            return dict(path=path, path_hash=path_hash, stat=stat, meta=meta, camera=camera,
                        lens=lens, key=key, media_error=media_error)

        def write_results(results):
            hashes = [result['path_hash'] for result in results]
            with Session.begin() as db:
                existing = {photo.path_hash: photo for photo in db.scalars(
                    select(Photo).where(Photo.path_hash.in_(hashes)))}
                stamp = now()
                for result in results:
                    path = result['path']
                    stat = result['stat']
                    photo = existing.get(result['path_hash'])
                    if photo is None:
                        photo = Photo(path_hash=result['path_hash'])
                        db.add(photo)
                    photo.path, photo.filename = str(path), path.name
                    photo.size, photo.mtime_ns = stat.st_size, stat.st_mtime_ns
                    photo.camera, photo.lens = result['camera'], result['lens']
                    photo.taken_at, photo.metadata_json = capture_date(result['meta']), result['meta']
                    photo.cache_key = result['key'] if not result['media_error'] else None
                    photo.preview_error = result['media_error']
                    photo.indexed_at = stamp

        def store_photos(results):
            if not results:
                return
            try:
                write_results(results)
                counts['indexed'] += len(results)
                counts['errors'] += sum(1 for result in results if result['media_error'])
                return
            except Exception as exc:
                log.warning('Batch database write failed; retrying photos individually: %s', exc)
            # Preserve as much completed work as possible if a single bad row breaks a batch.
            for result in results:
                try:
                    write_results([result])
                    counts['indexed'] += 1
                    if result['media_error']:
                        counts['errors'] += 1
                except Exception as exc:
                    counts['errors'] += 1
                    log.exception('Could not store indexed photo %s: %s', result['path'], exc)

        def prepare_batch(paths):
            """Read source state and EXIF for one batch without touching shared progress counters."""
            pending = []
            skipped = 0
            errors = []
            hashes = [digest(str(p)) for p in paths]
            with Session() as db:
                existing = {p.path_hash: p for p in db.scalars(select(Photo).where(Photo.path_hash.in_(hashes)))}
            for path, path_hash in zip(paths, hashes):
                old = existing.get(path_hash)
                if old and skip_imported:
                    skipped += 1
                    continue
                try:
                    stat = path.stat()
                    preview_ok = skip_previews
                    thumb_ok = False
                    if old and old.cache_key:
                        if not skip_previews:
                            preview_ok = (cache_file(preview_cache, old.cache_key, 'preview').is_file() or
                                          cache_file(legacy_cache, old.cache_key, 'preview').is_file())
                        thumb_ok = (cache_file(thumbnail_cache, old.cache_key, 'thumb').is_file() or
                                    cache_file(legacy_cache, old.cache_key, 'thumb').is_file())
                    if old and not force and old.size == stat.st_size and old.mtime_ns == stat.st_mtime_ns and preview_ok and thumb_ok:
                        skipped += 1
                    else:
                        pending.append((path, path_hash, stat))
                except OSError as exc:
                    errors.append((path, str(exc)))

            if not pending:
                return dict(pending=[], metadata={}, skipped=skipped, errors=errors, metadata_error=None)
            try:
                metadata = metadata_batch([p for p, _, _ in pending])
                metadata_error = None
            except Exception as exc:
                metadata = {}
                metadata_error = (len(pending), str(exc))
            return dict(pending=pending, metadata=metadata, skipped=skipped,
                        errors=errors, metadata_error=metadata_error)

        def process_prepared(prepared, executor):
            counts['skipped'] += prepared['skipped']
            for path, message in prepared['errors']:
                counts['errors'] += 1
                report(path, message)
            if prepared['metadata_error']:
                failed, message = prepared['metadata_error']
                counts['errors'] += failed
                report(message=f'Metadata batch failed: {message}', force=True)
                return

            pending = prepared['pending']
            if not pending:
                report()
                return
            metadata = prepared['metadata']
            futures = {}
            for path, path_hash, stat in pending:
                item = (path, path_hash, stat, metadata.get(str(path)))
                futures[executor.submit(render_photo, item)] = path
            completed = []
            try:
                for future in as_completed(futures):
                    path = futures[future]
                    report(path)
                    try:
                        completed.append(future.result())
                        if len(completed) >= DB_WRITE_BATCH_SIZE:
                            store_photos(completed)
                            completed = []
                    except Exception as exc:
                        counts['errors'] += 1
                        report(path, f'Could not index file: {exc}')
                        log.exception('Could not index %s', path)
                store_photos(completed)
            except InterruptedError:
                # Persist renders that already finished before honoring cancellation.
                store_photos(completed)
                for future in futures:
                    future.cancel()
                raise
            report()

        root = selected_scan_root()
        pending = []
        notes = []
        if skip_previews:
            notes.append('previews on demand')
        if skip_imported:
            notes.append('already imported paths skipped')
        if skip_post_stat:
            notes.append('fast SMB mode')
        notes.append(f'{parallelism} parallel worker' + ('s' if parallelism != 1 else ''))
        notes.append('metadata prefetch')
        report(message='Scanning folders' + (f" ({', '.join(notes)})" if notes else ''), force=True)

        def scan_batches():
            batch = []
            for directory, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
                directories[:] = [d for d in directories
                                   if d not in MACOS_GARBAGE_DIRS and not Path(directory, d).is_symlink()]
                report(directory)
                for filename in filenames:
                    if is_macos_garbage_name(filename):
                        continue
                    path = Path(directory, filename)
                    if path.suffix.lower() not in EXTENSIONS or path.is_symlink():
                        continue
                    counts['discovered'] += 1
                    batch.append(path)
                    if len(batch) >= SCAN_BATCH_SIZE:
                        yield batch
                        batch = []
            if batch:
                yield batch

        # One batch is prepared ahead while the current batch is being decoded/rendered.
        # Keeping a single metadata worker avoids multiple concurrent ExifTool metadata streams
        # and bounds SMB read-ahead to roughly one SCAN_BATCH_SIZE batch.
        with ThreadPoolExecutor(max_workers=parallelism, thread_name_prefix='raw-index') as executor,              ThreadPoolExecutor(max_workers=1, thread_name_prefix='raw-metadata') as metadata_executor:
            batches = iter(scan_batches())
            try:
                first_batch = next(batches)
            except StopIteration:
                first_batch = None
            if first_batch is not None:
                prepared_future = metadata_executor.submit(prepare_batch, first_batch)
                for next_batch in batches:
                    prepared = prepared_future.result()
                    prepared_future = metadata_executor.submit(prepare_batch, next_batch)
                    process_prepared(prepared, executor)
                process_prepared(prepared_future.result(), executor)
        completion = []
        if skip_previews:
            completion.append('previews will be generated when opened')
        if skip_imported:
            completion.append('existing source paths were skipped')
        if skip_post_stat:
            completion.append('post-read source verification was skipped')
        suffix = (' — ' + '; '.join(completion)) if completion else ''
        report(message=('Scan complete' if not counts['errors'] else 'Scan complete with errors; see worker logs') + suffix,
               force=True)
        state, message = 'done', last_message
    except InterruptedError as exc:
        state, message = 'cancelled', str(exc)
    except Exception as exc:
        state, message = 'failed', str(exc)
        log.exception('Scan failed')
    finally:
        close_exiftool_sessions()
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
        preview_edge, preview_quality = current_preview_settings() if kind == 'preview' else (None, None)
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
                        make_preview(source, photo.metadata_json or {}, output_root, photo.cache_key,
                                     preview_edge, preview_quality)
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


def clear_job_settings(job_id):
    with Session.begin() as db:
        for key in (SCAN_MODE_PREFIX + str(job_id), SCAN_SKIP_PREVIEWS_PREFIX + str(job_id),
                    SCAN_SKIP_IMPORTED_PREFIX + str(job_id), SCAN_SKIP_POST_STAT_PREFIX + str(job_id),
                    SCAN_PARALLELISM_PREFIX + str(job_id)):
            setting = db.get(Setting, key)
            if setting:
                db.delete(setting)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    Path(CACHE).mkdir(parents=True, exist_ok=True)
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
                clear_job_settings(job.id)
            else:
                time.sleep(2)
        except Exception:
            log.exception('Worker loop failed; retrying')
            time.sleep(5)


if __name__ == '__main__':
    main()
