import fcntl
import hmac
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, request, jsonify, session, send_file, abort
from sqlalchemy import select, func, or_, text
from werkzeug.exceptions import HTTPException
from app.db import Session, Photo, Scan, Setting, engine
from app.imaging import cache_file, make_preview
from app.statistics import build_statistics
from app.storage import (THUMB_FOLDER_KEY, PREVIEW_FOLDER_KEY, PREVIEW_EDGE_KEY,
                         PREVIEW_QUALITY_KEY, PREVIEW_EDGES, PREVIEW_QUALITIES,
                         cache_root, external_storage_root, configured_thumbnail_folder,
                         configured_preview_folder, configured_preview_edge,
                         configured_preview_quality, normalize_thumbnail_folder,
                         normalize_preview_folder, normalize_preview_edge,
                         normalize_preview_quality, resolve_thumbnail_root,
                         resolve_preview_root)

SELECTED_FOLDER_KEY = 'selected_photo_folder'
SCAN_MODE_PREFIX = 'scan_mode:'
SCAN_SKIP_PREVIEWS_PREFIX = 'scan_skip_previews:'


def configured_root():
    return Path(os.environ.get('PHOTO_ROOT', '/photos'))


def selected_relative(db):
    setting = db.get(Setting, SELECTED_FOLDER_KEY)
    return setting.value if setting else ''


def display_root(relative):
    return '/photos' + (f'/{relative}' if relative else '')


def resolve_folder(relative):
    if not isinstance(relative, str) or len(relative) > 4096:
        abort(400, 'Invalid folder path')
    requested = Path(relative)
    if requested.is_absolute() or '..' in requested.parts:
        abort(400, 'Folder must be inside the configured photo root')
    try:
        base = configured_root().resolve(strict=True)
    except OSError:
        abort(503, 'Configured photo root is unavailable')
    current = base
    clean_parts = []
    for part in requested.parts:
        if part in ('', '.'):
            continue
        candidate = current / part
        if candidate.is_symlink():
            abort(400, 'Symbolic links cannot be selected')
        current = candidate
        clean_parts.append(part)
    try:
        target = current.resolve(strict=True)
    except OSError:
        abort(404, 'Folder not found')
    if target != base and base not in target.parents:
        abort(400, 'Folder must be inside the configured photo root')
    if not target.is_dir():
        abort(400, 'Selection is not a folder')
    return target, Path(*clean_parts).as_posix() if clean_parts else ''


def resolve_original(path):
    try:
        base = configured_root().resolve(strict=True)
        source = Path(path)
        if source.is_symlink():
            raise OSError('Original is a symbolic link')
        target = source.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError('Original file is unavailable') from exc
    if target != base and base not in target.parents:
        raise RuntimeError('Original file is outside the configured photo root')
    if not target.is_file():
        raise RuntimeError('Original path is not a file')
    return target


def create_app():
    app = Flask(__name__, static_folder='static')
    secret = os.environ.get('SECRET_KEY')
    password = os.environ.get('CATALOG_PASSWORD')
    if not secret or len(secret) < 32 or not password or len(password) < 12:
        raise RuntimeError('Set SECRET_KEY (32+ characters) and CATALOG_PASSWORD (12+ characters)')
    app.config.update(SECRET_KEY=secret, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', 'false').lower() == 'true',
                      MAX_CONTENT_LENGTH=16384)

    @app.before_request
    def security():
        if request.path.startswith('/api/') or request.path.startswith('/media/'):
            if request.path not in ('/api/login', '/api/session') and not session.get('authenticated'):
                abort(401, 'Sign in to continue')
            if request.method in ('POST', 'DELETE', 'PUT', 'PATCH'):
                token = session.get('csrf', '')
                if not token or not hmac.compare_digest(token, request.headers.get('X-CSRF-Token', '')):
                    abort(403, 'Session expired. Reload the page and try again.')

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        if request.path.startswith('/media/'):
            response.headers['Cache-Control'] = 'private, max-age=3600'
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(Exception)
    def server_error(error):
        app.logger.exception('Request failed')
        return jsonify(error='Server error. Check the application logs.'), 500

    @app.get('/')
    def index():
        return app.send_static_file('index.html')

    @app.get('/settings')
    def settings_page():
        return app.send_static_file('settings.html')

    @app.get('/statistics')
    def statistics_page():
        return app.send_static_file('statistics.html')

    @app.get('/health')
    def health():
        with Session() as db:
            db.execute(text('SELECT 1'))
        return jsonify(ok=True)

    @app.get('/api/session')
    def auth_session():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(32)
        return jsonify(authenticated=bool(session.get('authenticated')), csrf=session['csrf'])

    @app.post('/api/login')
    def login():
        data = request.get_json(silent=True) or {}
        supplied = data.get('password', '')
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), password.encode()):
            abort(401, 'Incorrect password')
        session.clear()
        session.update(authenticated=True, csrf=secrets.token_hex(32))
        return jsonify(csrf=session['csrf'])

    @app.post('/api/logout')
    def logout():
        session.clear()
        return jsonify(ok=True)

    def date_arg(name):
        value = request.args.get(name, '').strip()
        if not value:
            return None
        try:
            return datetime.strptime(value, '%Y-%m-%d')
        except ValueError:
            abort(400, f'Invalid {name}; expected YYYY-MM-DD')

    def filters(exclude=None):
        clauses = []
        for name in ('camera', 'lens'):
            value = request.args.get(name, '')
            if value and name != exclude:
                clauses.append(getattr(Photo, name) == value[:190])
        q = request.args.get('q', '').strip()[:200]
        if q:
            clauses.append(or_(Photo.filename.contains(q, autoescape=True), Photo.path.contains(q, autoescape=True)))
        start = date_arg('date_from')
        end = date_arg('date_to')
        if start:
            clauses.append(Photo.taken_at >= start)
        if end:
            clauses.append(Photo.taken_at < end + timedelta(days=1))
        if start and end and start > end:
            abort(400, 'Start date must not be after end date')
        return clauses

    @app.get('/api/facets')
    def facets():
        result = {}
        with Session() as db:
            for name in ('camera', 'lens'):
                column = getattr(Photo, name)
                result[name] = [dict(value=value, count=count) for value, count in db.execute(
                    select(column, func.count()).where(*filters(exclude=name)).group_by(column).order_by(column))]
        return jsonify(result)

    @app.get('/api/photos')
    def photos():
        try:
            offset = max(0, int(request.args.get('after', 0)))
            limit = min(120, max(1, int(request.args.get('limit', 60))))
        except ValueError:
            abort(400, 'Invalid pagination')
        sort = request.args.get('sort', 'indexed_desc')
        orders = {
            'indexed_desc': (Photo.id.desc(),),
            'date_desc': (Photo.taken_at.is_(None), Photo.taken_at.desc(), Photo.id.desc()),
            'date_asc': (Photo.taken_at.is_(None), Photo.taken_at.asc(), Photo.id.asc()),
        }
        if sort not in orders:
            abort(400, 'Invalid sort order')
        with Session() as db:
            clauses = filters()
            total = db.scalar(select(func.count()).select_from(Photo).where(*clauses))
            query = select(Photo).where(*clauses).order_by(*orders[sort]).offset(offset).limit(limit + 1)
            rows = list(db.scalars(query))
            more = len(rows) > limit
            rows = rows[:limit]
            return jsonify(total=total, items=[serialize_photo(p) for p in rows],
                           next_cursor=(offset + limit) if more else None, sort=sort)

    @app.get('/api/photos/<int:photo_id>')
    def detail(photo_id):
        with Session() as db:
            p = db.get(Photo, photo_id)
            if not p:
                abort(404, 'Photo not found')
            return jsonify(**serialize_photo(p), path=p.path, metadata=p.metadata_json,
                           preview_error=p.preview_error, size=p.size)

    def generate_preview_on_demand(db, photo):
        try:
            root = resolve_preview_root(configured_preview_folder(db), create=True)
            edge = configured_preview_edge(db)
            quality = configured_preview_quality(db)
        except (ValueError, RuntimeError) as exc:
            abort(503, f'Preview storage is unavailable: {exc}')
        output = cache_file(root, photo.cache_key, 'preview')
        lock_dir = cache_root() / 'ondemand-locks'
        try:
            lock_dir.mkdir(parents=True, exist_ok=True)
            lock_path = lock_dir / f'{photo.cache_key}.lock'
            with open(lock_path, 'w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if output.is_file():
                    return output
                source = resolve_original(photo.path)
                make_preview(source, photo.metadata_json or {}, root, photo.cache_key, edge, quality)
        except Exception as exc:
            photo.preview_error = str(exc)[:2000]
            db.commit()
            app.logger.warning('On-demand preview failed for %s: %s', photo.path, exc)
            abort(500, f'Preview could not be generated: {exc}')
        if photo.preview_error:
            photo.preview_error = None
            db.commit()
        return output

    @app.get('/media/<int:photo_id>/<kind>')
    def media(photo_id, kind):
        if kind not in ('thumb', 'preview'):
            abort(404)
        with Session() as db:
            p = db.get(Photo, photo_id)
            if not p or not p.cache_key:
                abort(404, 'Generated media unavailable')
            path = None
            try:
                if kind == 'thumb':
                    root = resolve_thumbnail_root(configured_thumbnail_folder(db), create=False)
                    candidate = cache_file(root, p.cache_key, 'thumb')
                else:
                    root = resolve_preview_root(configured_preview_folder(db), create=False)
                    candidate = cache_file(root, p.cache_key, 'preview')
                if candidate.is_file():
                    path = candidate
            except (ValueError, RuntimeError):
                pass
            if path is None:
                legacy = cache_file(cache_root(), p.cache_key, kind)
                if legacy.is_file():
                    path = legacy
            if path is None and kind == 'preview':
                path = generate_preview_on_demand(db, p)
            if path is None:
                abort(404, 'Thumbnail missing; rebuild thumbnails from Settings')
            return send_file(path, mimetype='image/jpeg', conditional=True)

    def active_scan(db):
        return db.scalar(select(Scan).where(Scan.state.in_(['queued', 'running'])).order_by(Scan.id).limit(1))

    @app.get('/api/folders')
    def folders():
        target, relative = resolve_folder(request.args.get('path', ''))
        directories = []
        try:
            entries = sorted(target.iterdir(), key=lambda p: p.name.casefold())
        except OSError as exc:
            abort(403, f'Folder could not be read: {exc}')
        for entry in entries:
            try:
                if entry.is_dir() and not entry.is_symlink():
                    child = (Path(relative) / entry.name).as_posix() if relative else entry.name
                    directories.append(dict(name=entry.name, path=child))
            except OSError:
                continue
        parent = None
        if relative:
            parent_path = Path(relative).parent
            parent = '' if str(parent_path) == '.' else parent_path.as_posix()
        with Session() as db:
            selected = selected_relative(db)
        return jsonify(current=relative, current_display=display_root(relative), parent=parent,
                       selected=selected, selected_display=display_root(selected), directories=directories)

    @app.post('/api/folders/select')
    def select_folder():
        data = request.get_json(silent=True) or {}
        _, relative = resolve_folder(data.get('path', ''))
        with Session.begin() as db:
            if active_scan(db):
                abort(409, 'Cannot change the photo folder while a scan is active')
            setting = db.get(Setting, SELECTED_FOLDER_KEY)
            if setting is None:
                setting = Setting(key=SELECTED_FOLDER_KEY, value=relative)
                db.add(setting)
            else:
                setting.value = relative
        return jsonify(selected=relative, selected_display=display_root(relative))

    def generated_stats(db, kind, root, baseline):
        files = 0
        size = 0
        suffix = f'-{kind}.jpg'
        if root.is_dir() and not root.is_symlink():
            for directory, directories, filenames in os.walk(root, followlinks=False):
                directories[:] = [d for d in directories if not Path(directory, d).is_symlink()]
                for filename in filenames:
                    if not filename.endswith(suffix):
                        continue
                    path = Path(directory, filename)
                    try:
                        if path.is_file() and not path.is_symlink():
                            files += 1
                            size += path.stat().st_size
                    except OSError:
                        continue
        eligible = db.scalar(select(func.count()).select_from(Photo).where(Photo.cache_key.is_not(None))) or 0
        average = (size / files) if files else baseline
        return dict(path=str(root), files=files, bytes=size, eligible_photos=eligible,
                    estimated_bytes=int(average * eligible), estimated_per_file=int(average),
                    estimate_basis='current average' if files else 'baseline estimate')

    def thumbnail_stats(db):
        value = configured_thumbnail_folder(db)
        try:
            root = resolve_thumbnail_root(value, create=False)
        except (ValueError, RuntimeError) as exc:
            abort(500, f'Thumbnail folder is unavailable: {exc}')
        result = generated_stats(db, 'thumb', root, 60 * 1024)
        result.update(folder=value, estimated_per_thumbnail=result.pop('estimated_per_file'))
        if not result['files']:
            result['estimate_basis'] = '60 KB baseline'
        return result

    def preview_stats(db):
        value = configured_preview_folder(db)
        edge = configured_preview_edge(db)
        quality = configured_preview_quality(db)
        try:
            root = resolve_preview_root(value, create=False)
        except (ValueError, RuntimeError) as exc:
            abort(500, f'Preview folder is unavailable: {exc}')
        baseline = int(1.5 * 1024 * 1024 * (edge / 2560) ** 2 * (quality / 88))
        result = generated_stats(db, 'preview', root, baseline)
        result.update(folder=value, preview_edge=edge, preview_quality=quality,
                      estimated_per_preview=result.pop('estimated_per_file'))
        if not result['files']:
            result['estimate_basis'] = f'{edge}px quality {quality} baseline estimate'
        return result

    def storage_info():
        return dict(cache_root=str(cache_root()), external_root=str(external_storage_root()),
                    allowed_roots=[str(cache_root()), str(external_storage_root())])

    @app.get('/api/settings/thumbnails')
    def thumbnail_settings():
        with Session() as db:
            return jsonify(**thumbnail_stats(db), **storage_info(), active=bool(active_scan(db)))

    @app.put('/api/settings/thumbnails')
    def update_thumbnail_settings():
        data = request.get_json(silent=True) or {}
        try:
            value = normalize_thumbnail_folder(data.get('folder'))
            resolve_thumbnail_root(value, create=True)
        except ValueError as exc:
            abort(400, str(exc))
        except RuntimeError as exc:
            abort(503, str(exc))
        with Session.begin() as db:
            if active_scan(db):
                abort(409, 'Cannot change the thumbnail folder while a scan is active')
            setting = db.get(Setting, THUMB_FOLDER_KEY)
            if setting is None:
                db.add(Setting(key=THUMB_FOLDER_KEY, value=value))
            else:
                setting.value = value
        with Session() as db:
            return jsonify(**thumbnail_stats(db), **storage_info(), active=False)

    @app.get('/api/settings/previews')
    def preview_settings():
        with Session() as db:
            return jsonify(**preview_stats(db), **storage_info(), preview_edge_options=PREVIEW_EDGES,
                           preview_quality_options=PREVIEW_QUALITIES, active=bool(active_scan(db)))

    @app.put('/api/settings/previews')
    def update_preview_settings():
        data = request.get_json(silent=True) or {}
        with Session() as db:
            current_folder = configured_preview_folder(db)
            current_edge = configured_preview_edge(db)
            current_quality = configured_preview_quality(db)
        try:
            value = normalize_preview_folder(data.get('folder', current_folder))
            edge = normalize_preview_edge(data.get('preview_edge', current_edge))
            quality = normalize_preview_quality(data.get('preview_quality', current_quality))
            resolve_preview_root(value, create=True)
        except ValueError as exc:
            abort(400, str(exc))
        except RuntimeError as exc:
            abort(503, str(exc))
        with Session.begin() as db:
            if active_scan(db):
                abort(409, 'Cannot change preview settings while a scan is active')
            for key, setting_value in ((PREVIEW_FOLDER_KEY, value), (PREVIEW_EDGE_KEY, str(edge)),
                                       (PREVIEW_QUALITY_KEY, str(quality))):
                setting = db.get(Setting, key)
                if setting is None:
                    db.add(Setting(key=key, value=setting_value))
                else:
                    setting.value = setting_value
        with Session() as db:
            return jsonify(**preview_stats(db), **storage_info(), preview_edge_options=PREVIEW_EDGES,
                           preview_quality_options=PREVIEW_QUALITIES, active=False)

    def purge_tree(root, kind):
        removed = 0
        bytes_removed = 0
        suffix = f'-{kind}.jpg'
        if not root.is_dir() or root.is_symlink():
            return removed, bytes_removed
        for directory, directories, filenames in os.walk(root, topdown=True, followlinks=False):
            directories[:] = [d for d in directories if not Path(directory, d).is_symlink()]
            for filename in filenames:
                if not filename.endswith(suffix):
                    continue
                path = Path(directory, filename)
                try:
                    if path.is_file() and not path.is_symlink():
                        bytes_removed += path.stat().st_size
                        path.unlink()
                        removed += 1
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    abort(500, f'Could not purge generated cache: {exc}')
        for directory, _, _ in os.walk(root, topdown=False, followlinks=False):
            path = Path(directory)
            if path == root:
                continue
            try:
                path.rmdir()
            except OSError:
                pass
        return removed, bytes_removed

    def purge_generated(kind, current_root):
        roots = [current_root]
        legacy = cache_root()
        if legacy != current_root:
            roots.append(legacy)
        removed = bytes_removed = 0
        seen = set()
        for root in roots:
            resolved = root.resolve(strict=False)
            if resolved in seen:
                continue
            seen.add(resolved)
            count, size = purge_tree(root, kind)
            removed += count
            bytes_removed += size
        return removed, bytes_removed

    @app.delete('/api/cache/thumbnails')
    def purge_thumbnails():
        with Session() as db:
            if active_scan(db):
                abort(409, 'Cannot purge thumbnails while a scan is active')
            try:
                root = resolve_thumbnail_root(configured_thumbnail_folder(db), create=False)
            except (ValueError, RuntimeError) as exc:
                abort(503, str(exc))
        removed, bytes_removed = purge_generated('thumb', root)
        return jsonify(ok=True, removed=removed, bytes_removed=bytes_removed)

    @app.delete('/api/cache/previews')
    def purge_previews():
        with Session() as db:
            if active_scan(db):
                abort(409, 'Cannot purge previews while a scan is active')
            try:
                root = resolve_preview_root(configured_preview_folder(db), create=False)
            except (ValueError, RuntimeError) as exc:
                abort(503, str(exc))
        removed, bytes_removed = purge_generated('preview', root)
        return jsonify(ok=True, removed=removed, bytes_removed=bytes_removed)

    def serialize_scan(job):
        if not job:
            return None
        return {name: getattr(job, name) for name in ('id', 'state', 'discovered', 'indexed', 'skipped', 'errors', 'current_path', 'message', 'cancel')} | {'updated_at': job.updated_at.isoformat() + 'Z'}

    def queue_job(mode='scan', force=False, skip_previews=False):
        with engine.connect() as connection:
            mysql = connection.dialect.name == 'mysql'
            if mysql and connection.scalar(text("SELECT GET_LOCK('raw_catalog_scan_queue', 5)")) != 1:
                abort(409, 'Scan queue busy; try again')
            connection.commit()
            try:
                with Session(bind=connection) as db, db.begin():
                    if db.scalar(select(Scan).where(Scan.state.in_(['queued', 'running'])).limit(1)):
                        abort(409, 'A scan or rebuild is already queued or running')
                    messages = {'thumbnails': 'Thumbnail rebuild queued', 'previews': 'Preview rebuild queued'}
                    default_message = 'Waiting for indexer (previews on demand)' if skip_previews else 'Waiting for indexer'
                    job = Scan(force=force, message=messages.get(mode, default_message))
                    db.add(job)
                    db.flush()
                    if mode != 'scan':
                        db.add(Setting(key=SCAN_MODE_PREFIX + str(job.id), value=mode))
                    if mode == 'scan' and skip_previews:
                        db.add(Setting(key=SCAN_SKIP_PREVIEWS_PREFIX + str(job.id), value='1'))
                    result = serialize_scan(job)
            finally:
                if mysql:
                    connection.execute(text("SELECT RELEASE_LOCK('raw_catalog_scan_queue')"))
                    connection.commit()
        return result

    @app.post('/api/cache/thumbnails/rebuild')
    def rebuild_thumbnail_cache():
        return jsonify(scan=queue_job('thumbnails')), 202

    @app.post('/api/cache/previews/rebuild')
    def rebuild_preview_cache():
        return jsonify(scan=queue_job('previews')), 202

    @app.get('/api/statistics')
    def statistics():
        force = request.args.get('refresh') == '1'
        with Session.begin() as db:
            return jsonify(build_statistics(db, force=force))

    @app.get('/api/scan')
    def scan_status():
        with Session() as db:
            active = active_scan(db)
            job = active or db.scalar(select(Scan).order_by(Scan.id.desc()).limit(1))
            selected = selected_relative(db)
            return jsonify(scan=serialize_scan(job), root=display_root(selected), selected=selected)

    @app.post('/api/scan')
    def start_scan():
        data = request.get_json(silent=True) or {}
        return jsonify(scan=queue_job('scan', force=data.get('force') is True,
                                      skip_previews=data.get('skip_previews') is True)), 202

    @app.post('/api/scan/cancel')
    def cancel_scan():
        with Session.begin() as db:
            jobs = list(db.scalars(select(Scan).where(Scan.state.in_(['queued', 'running']))))
            for job in jobs:
                job.cancel = True
        return jsonify(ok=True)

    return app


def serialize_photo(p):
    return dict(id=p.id, filename=p.filename, camera=p.camera, lens=p.lens,
                taken_at=p.taken_at.isoformat() if p.taken_at else None,
                thumbnail=f'/media/{p.id}/thumb?v={p.cache_key}' if p.cache_key else None,
                preview=f'/media/{p.id}/preview?v={p.cache_key}' if p.cache_key else None)
