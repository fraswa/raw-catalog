import hmac
import os
import secrets
from pathlib import Path
from flask import Flask, request, jsonify, session, send_file, abort
from sqlalchemy import select, func, or_, text
from werkzeug.exceptions import HTTPException
from app.db import Session, Photo, Scan, engine, init_db
from app.imaging import cache_file


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
            path = cache_file(os.environ.get('CACHE_DIR', '/data/cache'), p.cache_key, kind)
            if not path.is_file():
                abort(404, 'Preview missing; run a scan to regenerate it')
            return send_file(path, mimetype='image/jpeg', conditional=True)

    def serialize_scan(job):
        if not job:
            return None
        return {name: getattr(job, name) for name in ('id', 'state', 'discovered', 'indexed', 'skipped', 'errors', 'current_path', 'message', 'cancel')} | {'updated_at': job.updated_at.isoformat() + 'Z'}

    @app.get('/api/scan')
    def scan_status():
        with Session() as db:
            active = db.scalar(select(Scan).where(Scan.state.in_(['queued', 'running'])).order_by(Scan.id).limit(1))
            job = active or db.scalar(select(Scan).order_by(Scan.id.desc()).limit(1))
            return jsonify(scan=serialize_scan(job), root=os.environ.get('PHOTO_ROOT', '/photos'))

    @app.post('/api/scan')
    def start_scan():
        data = request.get_json(silent=True) or {}
        with engine.connect() as connection:
            mysql = connection.dialect.name == 'mysql'
            if mysql and connection.scalar(text("SELECT GET_LOCK('raw_catalog_scan_queue', 5)")) != 1:
                abort(409, 'Scan queue busy; try again')
            connection.commit()
            try:
                with Session(bind=connection) as db, db.begin():
                    active = db.scalar(select(Scan).where(Scan.state.in_(['queued', 'running'])).limit(1))
                    if active:
                        abort(409, 'A scan is already queued or running')
                    job = Scan(force=data.get('force') is True)
                    db.add(job)
                    db.flush()
                    result = serialize_scan(job)
            finally:
                if mysql:
                    connection.execute(text("SELECT RELEASE_LOCK('raw_catalog_scan_queue')"))
                    connection.commit()
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
