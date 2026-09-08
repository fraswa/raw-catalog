from pathlib import Path


def replace_once(path, old, new):
    path = Path(path)
    text = path.read_text()
    if old not in text:
        raise RuntimeError(f'expected text not found in {path}: {old[:160]!r}')
    path.write_text(text.replace(old, new, 1))


old_process = '''        def process(paths, executor):
            pending = []
            hashes = [digest(str(p)) for p in paths]
            with Session() as db:
                existing = {p.path_hash: p for p in db.scalars(select(Photo).where(Photo.path_hash.in_(hashes)))}
            for path, path_hash in zip(paths, hashes):
                old = existing.get(path_hash)
                if old and skip_imported:
                    counts['skipped'] += 1
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
                report(message=f'Metadata batch failed: {exc}', force=True)
                return

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
'''

new_process = '''        def prepare_batch(paths):
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
'''

replace_once('app/worker.py', old_process, new_process)

old_loop = '''        report(message='Scanning folders' + (f" ({', '.join(notes)})" if notes else ''), force=True)
        with ThreadPoolExecutor(max_workers=parallelism, thread_name_prefix='raw-index') as executor:
            for directory, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
                directories[:] = [d for d in directories if not Path(directory, d).is_symlink()]
                report(directory)
                for filename in filenames:
                    path = Path(directory, filename)
                    if path.suffix.lower() not in EXTENSIONS or path.is_symlink():
                        continue
                    counts['discovered'] += 1
                    pending.append(path)
                    if len(pending) >= SCAN_BATCH_SIZE:
                        process(pending, executor)
                        pending = []
            if pending:
                process(pending, executor)
'''

new_loop = '''        notes.append('metadata prefetch')
        report(message='Scanning folders' + (f" ({', '.join(notes)})" if notes else ''), force=True)

        def scan_batches():
            batch = []
            for directory, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
                directories[:] = [d for d in directories if not Path(directory, d).is_symlink()]
                report(directory)
                for filename in filenames:
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
        with ThreadPoolExecutor(max_workers=parallelism, thread_name_prefix='raw-index') as executor, \
             ThreadPoolExecutor(max_workers=1, thread_name_prefix='raw-metadata') as metadata_executor:
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
'''

replace_once('app/worker.py', old_loop, new_loop)

# Add a concurrency regression test proving metadata for batch N+1 overlaps rendering batch N.
test_path = Path('tests/test_preview_on_demand.py')
text = test_path.read_text()
marker = 'def test_metadata_prefetch_overlaps_rendering('
if marker not in text:
    text += r'''


def test_metadata_prefetch_overlaps_rendering(tmp_path, monkeypatch):
    photos = tmp_path / 'photos'
    photos.mkdir()
    for index in range(worker.SCAN_BATCH_SIZE + 1):
        (photos / f'{index:03d}.cr3').write_bytes(b'raw')
    cache = tmp_path / 'cache'
    external = tmp_path / 'storage'
    external.mkdir()
    monkeypatch.setenv('CACHE_DIR', str(cache))
    monkeypatch.setenv('EXTERNAL_STORAGE_ROOT', str(external))
    monkeypatch.setattr(worker, 'ROOT', photos)
    monkeypatch.setattr(worker, 'CACHE', str(cache))

    render_started = threading.Event()
    lock = threading.Lock()
    active_renders = 0
    metadata_calls = 0
    overlapped = False

    def fake_metadata(paths):
        nonlocal metadata_calls, overlapped
        metadata_calls += 1
        if metadata_calls == 2:
            assert render_started.wait(2), 'second metadata batch did not overlap render startup'
            with lock:
                overlapped = active_renders > 0
        return {str(path): {'Make': 'Canon', 'Model': 'EOS R6', 'LensModel': 'Test lens'} for path in paths}

    def fake_thumbnail(path, metadata, root, key):
        nonlocal active_renders
        with lock:
            active_renders += 1
            render_started.set()
        try:
            time.sleep(0.02)
            output = cache_file(root, key, 'thumb')
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b'thumb')
        finally:
            with lock:
                active_renders -= 1

    monkeypatch.setattr(worker, 'metadata_batch', fake_metadata)
    monkeypatch.setattr(worker, 'make_thumbnail', fake_thumbnail)

    with Session.begin() as db:
        job = Scan()
        db.add(job)
        db.flush()
        job_id = job.id
        db.add(Setting(key=f'scan_skip_previews:{job_id}', value='1'))
        db.add(Setting(key=f'scan_parallelism:{job_id}', value='4'))

    worker.scan(job_id)

    with Session() as db:
        job = db.get(Scan, job_id)
        assert job.state == 'done'
        assert job.indexed == worker.SCAN_BATCH_SIZE + 1
        assert job.errors == 0
    assert metadata_calls == 2
    assert overlapped is True
'''
    test_path.write_text(text)
