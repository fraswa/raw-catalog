import hmac
import os
import secrets
from pathlib import Path
from flask import Flask, request, jsonify, session, send_file, abort
from sqlalchemy import select, func, or_, text
from werkzeug.exceptions import HTTPException
from app.db import Session, Photo, Scan, Setting, engine
from app.imaging import cache_file
from app.storage import (THUMB_FOLDER_KEY, cache_root, configured_thumbnail_folder,
                         normalize_thumbnail_folder, resolve_thumbnail_root)


SELECTED_FOLDER_KEY = 'selected_photo_folder'
SCAN_MODE_PREFIX = 'scan_mode:'


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

    def filters(exclude=None):
        clauses = []
        for name in ('camera', 'lens'):
            value = request.args.get(name, '')
            if value and name != exclude:
                clauses.append(getattr(Photo, name) == value[:190])
        q = request.args.get('q', '').strip()[:200]
        if q:
            clauses.append(or_(Photo.filename.contains(q, autoescape=True), Photo.path.contains(q, autoescape=True)))
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
            after = max(0, int(request.args.get('after', 0)))
            limit = min(120, max(1, int(request.args.get('limit', 60))))
        except ValueError:
            abort(400, 'Invalid pagination')
        with Session() as db:
            clauses = filters()
            total = db.scalar(select(func.count()).select_from(Photo).where(*clauses))
            query = select(Photo).where(*clauses)
            if after:
                query = query.where(Photo.id < after)
            rows = list(db.scalars(query.order_by(Photo.id.desc()).limit(limit + 1)))
            more = len(rows) > limit
            rows = rows[:limit]
            return jsonify(total=total, items=[serialize_photo(p) for p in rows],
                           next_cursor=rows[-1].id if more else None)

    @app.get('/api/photos/<int:photo_id>')
    def detail(photo_id):
        with Session() as db:
            p = db.get(Photo, photo_id)
            if not p:
                abort(404, 'Photo not found')
            return jsonify(**serialize_photo(p), path=p.path, metadata=p.metadata_json,
                           preview_error=p.preview_error, size=p.size)

    @app.get('/media/<int:photo_id>/<kind>')
    def media(photo_id, kind):
        if kind not in ('thumb', 'preview'):
            abort(404)
        with Session() as db:
            p = db.get(Photo, photo_id)
            if not p or not p.cache_key:
                abort(404, 'Preview unavailable')
            if kind == 'thumb':
                relative = configured_thumbnail_folder(db)
                try:
                    root = resolve_thumbnail_root(relative, create=False)
                except (ValueError, RuntimeError):
                    abort(500, 'Thumbnail folder is unavailable')
                path = cache_file(root, p.cache_key, 'thumb')
                if not path.is_file():
                    legacy = cache_file(cache_root(), p.cache_key, 'thumb')
                    path = legacy if legacy.is_file() else path
            else:
                path = cache_file(cache_root(), p.cache_key, 'preview')
            if not path.is_file():
                abort(404, 'Preview missing; run a scan or thumbnail rebuild to regenerate it')
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

    def thumbnail_stats(db):
        relative = configured_thumbnail_folder(db)
        try:
            root = resolve_thumbnail_root(relative, create=False)
        except FileNotFoundError:
            root = cache_root() / relative
        except (ValueError, RuntimeError) as exc:
            abort(500, f'Thumbnail folder is unavailable: {exc}')
        files = 0
        size = 0
        if root.is_dir() and not root.is_symlink():
            for directory, directories, filenames in os.walk(root, followlinks=False):
                directories[:] = [d for d in directories if not Path(directory, d).is_symlink()]
                for filename in filenames:
                    if not filename.endswith('-thumb.jpg'):
                        continue
                    path = Path(directory, filename)
                    try:
                        if path.is_file() and not path.is_symlink():
                            files += 1
                            size += path.stat().st_size
                    except OSError:
                        continue
        eligible = db.scalar(select(func.count()).select_from(Photo).where(Photo.cache_key.is_not(None))) or 0
        average = (size / files) if files else 60 * 1024
        return dict(folder=relative, path=str(root), files=files, bytes=size,
                    eligible_photos=eligible, estimated_bytes=int(average * eligible),
                    estimated_per_thumbnail=int(average),
                    estimate_basis='current average' if files else '60 KB baseline')

    @app.get('/api/settings/thumbnails')
    def thumbnail_settings():
        with Session() as db:
            return jsonify(**thumbnail_stats(db), cache_root=str(cache_root()), active=bool(active_scan(db)))

    @app.put('/api/settings/thumbnails')
    def update_thumbnail_settings():
        data = request.get_json(silent=True) or {}
        try:
            relative = normalize_thumbnail_folder(data.get('folder'))
            resolve_thumbnail_root(relative, create=True)
        except ValueError as exc:
            abort(400, str(exc))
        except RuntimeError as exc:
            abort(503, str(exc))
        with Session.begin() as db:
            if active_scan(db):
                abort(409, 'Cannot change the thumbnail folder while a scan is active')
            setting = db.get(Setting, THUMB_FOLDER_KEY)
            if setting is None:
                db.add(Setting(key=THUMB_FOLDER_KEY, value=relative))
            else:
                setting.value = relative
        with Session() as db:
            return jsonify(**thumbnail_stats(db), cache_root=str(cache_root()), active=False)

    @app.delete('/api/cache/thumbnails')
    def purge_thumbnails():
        with Session() as db:
            if active_scan(db):
                abort(409, 'Cannot purge thumbnails while a scan is active')
        cache = cache_root()
        removed = 0
        bytes_removed = 0
        if cache.is_dir() and not cache.is_symlink():
            for directory, directories, filenames in os.walk(cache, topdown=True, followlinks=False):
                directories[:] = [d for d in directories if not Path(directory, d).is_symlink()]
                for filename in filenames:
                    if not filename.endswith('-thumb.jpg'):
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
                        abort(500, f'Could not purge thumbnail cache: {exc}')
            for directory, directories, filenames in os.walk(cache, topdown=False, followlinks=False):
                path = Path(directory)
                if path == cache:
                    continue
                try:
                    path.rmdir()
                except OSError:
                    pass
        return jsonify(ok=True, removed=removed, bytes_removed=bytes_removed)

    def serialize_scan(job):
        if not job:
            return None
        return {name: getattr(job, name) for name in ('id', 'state', 'discovered', 'indexed', 'skipped', 'errors', 'current_path', 'message', 'cancel')} | {'updated_at': job.updated_at.isoformat() + 'Z'}

    def queue_job(mode='scan', force=False):
        with engine.connect() as connection:
            mysql = connection.dialect.name == 'mysql'
            if mysql and connection.scalar(text("SELECT GET_LOCK('raw_catalog_scan_queue', 5)")) != 1:
                abort(409, 'Scan queue busy; try again')
            connection.commit()
            try:
                with Session(bind=connection) as db, db.begin():
                    if db.scalar(select(Scan).where(Scan.state.in_(['queued', 'running'])).limit(1)):
                        abort(409, 'A scan or rebuild is already queued or running')
                    job = Scan(force=force, message='Thumbnail rebuild queued' if mode == 'thumbnails' else 'Waiting for indexer')
                    db.add(job)
                    db.flush()
                    if mode != 'scan':
                        db.add(Setting(key=SCAN_MODE_PREFIX + str(job.id), value=mode))
                    result = serialize_scan(job)
            finally:
                if mysql:
                    connection.execute(text("SELECT RELEASE_LOCK('raw_catalog_scan_queue')"))
                    connection.commit()
        return result

    @app.post('/api/cache/thumbnails/rebuild')
    def rebuild_thumbnail_cache():
        result = queue_job('thumbnails')
        return jsonify(scan=result), 202

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
        result = queue_job('scan', force=data.get('force') is True)
        return jsonify(scan=result), 202

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
