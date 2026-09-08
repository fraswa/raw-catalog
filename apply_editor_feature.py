from pathlib import Path


def replace(path, old, new):
    p = Path(path)
    text = p.read_text()
    if old not in text:
        raise SystemExit(f'marker not found in {path}: {old[:80]!r}')
    p.write_text(text.replace(old, new, 1))


# Storage: permanent edited-JPEG destination under either allowed storage root.
replace('app/storage.py',
"PREVIEW_QUALITY_KEY = 'preview_quality'\nDEFAULT_THUMB_FOLDER = 'thumbnails'\nDEFAULT_PREVIEW_FOLDER = 'previews'\n",
"PREVIEW_QUALITY_KEY = 'preview_quality'\nEDIT_FOLDER_KEY = 'edit_folder'\nDEFAULT_THUMB_FOLDER = 'thumbnails'\nDEFAULT_PREVIEW_FOLDER = 'previews'\nDEFAULT_EDIT_FOLDER = 'edits'\n")
replace('app/storage.py',
"def normalize_preview_folder(value):\n    return _normalize_folder(value, DEFAULT_PREVIEW_FOLDER, 'preview')\n\n\n",
"def normalize_preview_folder(value):\n    return _normalize_folder(value, DEFAULT_PREVIEW_FOLDER, 'preview')\n\n\ndef normalize_edit_folder(value):\n    return _normalize_folder(value, DEFAULT_EDIT_FOLDER, 'edit')\n\n\n")
replace('app/storage.py',
"def configured_preview_folder(db):\n    setting = db.get(Setting, PREVIEW_FOLDER_KEY)\n    return normalize_preview_folder(setting.value if setting else None)\n\n\n",
"def configured_preview_folder(db):\n    setting = db.get(Setting, PREVIEW_FOLDER_KEY)\n    return normalize_preview_folder(setting.value if setting else None)\n\n\ndef configured_edit_folder(db):\n    setting = db.get(Setting, EDIT_FOLDER_KEY)\n    return normalize_edit_folder(setting.value if setting else None)\n\n\n")
replace('app/storage.py',
"def resolve_preview_root(value, create=False):\n    return _resolve_root(value, normalize_preview_folder, 'preview', create=create)\n",
"def resolve_preview_root(value, create=False):\n    return _resolve_root(value, normalize_preview_folder, 'preview', create=create)\n\n\ndef resolve_edit_root(value, create=False):\n    return _resolve_root(value, normalize_edit_folder, 'edit', create=create)\n")

# Web backend imports.
replace('app/web.py',
"from app.imaging import cache_file, make_preview\nfrom app.statistics import build_statistics\n",
"from app.imaging import cache_file, make_preview\nfrom app.editor import (normalize_settings as normalize_editor_settings, render_preview as render_editor_preview,\n                        auto_settings as auto_editor_settings, save_jpeg as save_editor_jpeg)\nfrom app.statistics import build_statistics\n")
replace('app/web.py',
"from app.storage import (THUMB_FOLDER_KEY, PREVIEW_FOLDER_KEY, PREVIEW_EDGE_KEY,\n                         PREVIEW_QUALITY_KEY, PREVIEW_EDGES, PREVIEW_QUALITIES,\n                         cache_root, external_storage_root, configured_thumbnail_folder,\n                         configured_preview_folder, configured_preview_edge,\n                         configured_preview_quality, normalize_thumbnail_folder,\n                         normalize_preview_folder, normalize_preview_edge,\n                         normalize_preview_quality, resolve_thumbnail_root,\n                         resolve_preview_root)\n",
"from app.storage import (THUMB_FOLDER_KEY, PREVIEW_FOLDER_KEY, PREVIEW_EDGE_KEY,\n                         PREVIEW_QUALITY_KEY, EDIT_FOLDER_KEY, PREVIEW_EDGES, PREVIEW_QUALITIES,\n                         cache_root, external_storage_root, configured_thumbnail_folder,\n                         configured_preview_folder, configured_edit_folder, configured_preview_edge,\n                         configured_preview_quality, normalize_thumbnail_folder,\n                         normalize_preview_folder, normalize_edit_folder, normalize_preview_edge,\n                         normalize_preview_quality, resolve_thumbnail_root,\n                         resolve_preview_root, resolve_edit_root)\n")

old_media = """    @app.get('/media/<int:photo_id>/<kind>')
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

"""
new_media = """    @app.get('/media/<int:photo_id>/<kind>')
    def media(photo_id, kind):
        if kind not in ('thumb', 'preview', 'original'):
            abort(404)
        with Session() as db:
            p = db.get(Photo, photo_id)
            if not p:
                abort(404, 'Photo not found')
            if kind == 'original':
                try:
                    source = resolve_original(p.path)
                except RuntimeError as exc:
                    abort(404, str(exc))
                return send_file(source, mimetype='application/octet-stream', conditional=True,
                                 as_attachment=True, download_name=source.name)
            if not p.cache_key:
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
            download = request.args.get('download') == '1'
            name = f'{Path(p.filename).stem}-{kind}.jpg' if download else None
            return send_file(path, mimetype='image/jpeg', conditional=True,
                             as_attachment=download, download_name=name)

    def editor_source(photo_id):
        with Session() as db:
            photo = db.get(Photo, photo_id)
            if not photo:
                abort(404, 'Photo not found')
            metadata = photo.metadata_json or {}
            filename = photo.filename
            path = photo.path
        try:
            return resolve_original(path), metadata, filename
        except RuntimeError as exc:
            abort(404, str(exc))

    @app.post('/api/photos/<int:photo_id>/editor/preview')
    def raw_editor_preview(photo_id):
        source, _, _ = editor_source(photo_id)
        try:
            settings = normalize_editor_settings(request.get_json(silent=True) or {})
            output = render_editor_preview(source, settings)
        except ValueError as exc:
            abort(400, str(exc))
        except Exception as exc:
            app.logger.warning('RAW editor preview failed for %s: %s', source, exc)
            abort(500, f'RAW editor could not render this file: {exc}')
        return send_file(output, mimetype='image/jpeg', conditional=False)

    @app.post('/api/photos/<int:photo_id>/editor/auto')
    def raw_editor_auto(photo_id):
        source, metadata, _ = editor_source(photo_id)
        try:
            return jsonify(settings=auto_editor_settings(source, metadata))
        except Exception as exc:
            app.logger.warning('RAW editor auto settings failed for %s: %s', source, exc)
            abort(500, f'Auto adjustment failed: {exc}')

    @app.post('/api/photos/<int:photo_id>/editor/save')
    def raw_editor_save(photo_id):
        source, _, source_name = editor_source(photo_id)
        try:
            settings = normalize_editor_settings(request.get_json(silent=True) or {})
        except ValueError as exc:
            abort(400, str(exc))
        with Session() as db:
            folder_value = configured_edit_folder(db)
        try:
            root = resolve_edit_root(folder_value, create=True)
        except (ValueError, RuntimeError) as exc:
            abort(503, f'Edited JPEG storage is unavailable: {exc}')
        safe_stem = ''.join(c if c.isalnum() or c in ('-', '_', '.') else '_' for c in Path(source_name).stem)[:120] or 'photo'
        stamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
        filename = f'{safe_stem}-edit-{stamp}-{secrets.token_hex(3)}.jpg'
        output = root / filename
        try:
            save_editor_jpeg(source, settings, output)
        except Exception as exc:
            app.logger.warning('RAW editor save failed for %s: %s', source, exc)
            abort(500, f'Edited JPEG could not be saved: {exc}')
        return jsonify(filename=filename, folder=str(root), download_url=f'/media/edit/{filename}?download=1')

    @app.get('/media/edit/<filename>')
    def edited_media(filename):
        if filename != Path(filename).name or not filename.lower().endswith('.jpg'):
            abort(404)
        with Session() as db:
            value = configured_edit_folder(db)
        try:
            root = resolve_edit_root(value, create=False)
            path = root / filename
            if path.is_symlink() or not path.is_file() or path.resolve(strict=True).parent != root:
                abort(404, 'Edited JPEG not found')
        except (ValueError, RuntimeError, OSError):
            abort(404, 'Edited JPEG not found')
        return send_file(path, mimetype='image/jpeg', conditional=True,
                         as_attachment=request.args.get('download') == '1', download_name=filename)

"""
replace('app/web.py', old_media, new_media)

# Edited JPEG storage statistics and settings API.
marker = """    def storage_info():
        return dict(cache_root=str(cache_root()), external_root=str(external_storage_root()),
                    allowed_roots=[str(cache_root()), str(external_storage_root())])

"""
addition = """    def edit_stats(db):
        value = configured_edit_folder(db)
        try:
            root = resolve_edit_root(value, create=False)
        except (ValueError, RuntimeError) as exc:
            abort(500, f'Edited JPEG folder is unavailable: {exc}')
        files = 0
        size = 0
        if root.is_dir() and not root.is_symlink():
            for directory, directories, filenames in os.walk(root, followlinks=False):
                directories[:] = [d for d in directories if not Path(directory, d).is_symlink()]
                for filename in filenames:
                    if not filename.lower().endswith('.jpg'):
                        continue
                    path = Path(directory, filename)
                    try:
                        if path.is_file() and not path.is_symlink():
                            files += 1
                            size += path.stat().st_size
                    except OSError:
                        continue
        return dict(folder=value, path=str(root), files=files, bytes=size)

""" + marker
replace('app/web.py', marker, addition)

preview_put_end = """        with Session() as db:
            return jsonify(**preview_stats(db), **storage_info(), preview_edge_options=PREVIEW_EDGES,
                           preview_quality_options=PREVIEW_QUALITIES, active=False)

"""
edit_api = preview_put_end + """    @app.get('/api/settings/edits')
    def edit_settings():
        with Session() as db:
            return jsonify(**edit_stats(db), **storage_info(), active=bool(active_scan(db)))

    @app.put('/api/settings/edits')
    def update_edit_settings():
        data = request.get_json(silent=True) or {}
        try:
            value = normalize_edit_folder(data.get('folder'))
            resolve_edit_root(value, create=True)
        except ValueError as exc:
            abort(400, str(exc))
        except RuntimeError as exc:
            abort(503, str(exc))
        with Session.begin() as db:
            if active_scan(db):
                abort(409, 'Cannot change the edited JPEG folder while a scan or rebuild is active')
            setting = db.get(Setting, EDIT_FOLDER_KEY)
            if setting is None:
                db.add(Setting(key=EDIT_FOLDER_KEY, value=value))
            else:
                setting.value = value
        with Session() as db:
            return jsonify(**edit_stats(db), **storage_info(), active=False)

"""
replace('app/web.py', preview_put_end, edit_api)

# Library UI: downloads and editor.
replace('app/static/index.html',
'<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><title>RAW Catalog</title><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/controls.css"><script defer src="/static/app.js"></script></head>',
'<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><title>RAW Catalog</title><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/controls.css"><link rel="stylesheet" href="/static/editor.css"><script defer src="/static/editor.js"></script><script defer src="/static/app.js"></script></head>')
replace('app/static/index.html',
'<div><button id="fullScreen" title="Enter full screen">Full screen</button><button id="closeViewer" aria-label="Close preview">Close ×</button></div>',
'<div class="viewer-actions"><a id="downloadPreview" class="button-link" href="#">Preview JPEG</a><a id="downloadRaw" class="button-link" href="#">Original RAW</a><button id="editRaw">RAW Editor</button><button id="fullScreen" title="Enter full screen">Full screen</button><button id="closeViewer" aria-label="Close preview">Close ×</button></div>')
editor_dialog = """
<dialog id="editorDialog" aria-labelledby="editorTitle"><div class="editor-layout">
<div class="editor-top"><div><strong id="editorTitle">RAW Editor</strong><span>Non-destructive RAW processing</span></div><button id="closeEditor">Close ×</button></div>
<div class="editor-body"><div class="editor-stage"><img id="editorImage" alt="Edited RAW preview" hidden><p id="editorPreviewMessage">Preparing RAW preview…</p></div>
<aside class="editor-controls"><div class="editor-actions"><button id="autoEdit" class="primary">Auto</button><button id="resetEdit">Reset</button></div>
<div class="editor-group">
<div class="editor-control"><label for="editExposure">Exposure</label><output id="editExposureValue">0.0 EV</output><input id="editExposure" type="range" min="-4" max="4" step="0.1" value="0"></div>
<div class="editor-control"><label for="editTemperature">Temperature</label><output id="editTemperatureValue">6500 K</output><input id="editTemperature" type="range" min="2000" max="12000" step="100" value="6500"></div>
<div class="editor-control"><label for="editTint">Tint</label><output id="editTintValue">0</output><input id="editTint" type="range" min="-100" max="100" step="1" value="0"></div>
</div>
<div class="editor-group">
<div class="editor-control"><label for="editBlack">Levels — black</label><output id="editBlackValue">0</output><input id="editBlack" type="range" min="0" max="80" step="1" value="0"></div>
<div class="editor-control"><label for="editWhite">Levels — white</label><output id="editWhiteValue">255</output><input id="editWhite" type="range" min="128" max="255" step="1" value="255"></div>
<div class="editor-control"><label for="editDenoise">Simple denoise</label><output id="editDenoiseValue">0%</output><input id="editDenoise" type="range" min="0" max="100" step="10" value="0"></div>
</div>
<p class="editor-note">Auto analyzes the RAW for conservative exposure, levels, and ISO-based denoise. Temperature/tint are relative adjustments around the camera white balance.</p>
<div class="editor-save"><button id="saveEdit" class="primary">Save full-resolution JPEG</button><a id="downloadEdit" class="button-link" href="#" hidden>Download saved JPEG</a><p id="editorStatus" class="editor-status"></p></div>
</aside></div></div></dialog>
"""
replace('app/static/index.html', '</body></html>', editor_dialog + '</body></html>')

# Wire current photo to download links/editor.
replace('app/static/app.js',
"function showLogin(){authenticated=false;$('application').hidden=true;$('login').hidden=false;if($('viewer').open)$('viewer').close();}",
"function showLogin(){authenticated=false;$('application').hidden=true;$('login').hidden=false;if(window.rawCatalogEditor)window.rawCatalogEditor.close();if($('viewer').open)$('viewer').close();}")
needle = "viewing=index;const photo=items[index],rev=++detailRevision;$('viewerTitle').textContent=photo.filename;"
injected = "viewing=index;const photo=items[index],rev=++detailRevision;$('downloadPreview').hidden=!photo.preview;if(photo.preview){$('downloadPreview').href=`/media/${photo.id}/preview?download=1`;$('downloadPreview').download=`${photo.filename.replace(/\\.[^.]+$/,'')}-preview.jpg`;}$('downloadRaw').href=`/media/${photo.id}/original?download=1`;$('downloadRaw').download=photo.filename;$('editRaw').onclick=()=>window.rawCatalogEditor?.open(photo,csrf);$('viewerTitle').textContent=photo.filename;"
replace('app/static/app.js', needle, injected)

# Settings UI for edited JPEG destination.
replace('app/static/settings.html',
'<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><title>RAW Catalog Settings</title><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/controls.css"><link rel="stylesheet" href="/static/settings.css"><script defer src="/static/settings.js"></script></head>',
'<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><title>RAW Catalog Settings</title><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/controls.css"><link rel="stylesheet" href="/static/settings.css"><script defer src="/static/settings.js"></script><script defer src="/static/edit-settings.js"></script></head>')
worker_marker = """<section class="settings-card">
<div class="settings-heading"><div><p class="eyebrow">WORKER</p><h2>Current job</h2></div></div>
"""
edit_section = """<section class="settings-card">
<div class="settings-heading"><div><p class="eyebrow">EDITED JPEGS</p><h2>RAW editor output</h2></div></div>
<p class="settings-copy">JPEGs saved from the RAW editor have their own permanent output location. Originals, thumbnails, and preview cache files are never modified.</p>
<form id="editFolderForm" class="settings-form"><label for="editFolder">Edited JPEG path</label><div class="inline-form"><input id="editFolder" type="text" maxlength="2048" autocomplete="off" spellcheck="false" placeholder="/storage/edits" required><button id="saveEditFolder" class="primary" type="submit">Change folder</button></div><p id="editStorageHint" class="hint">Examples: <code>/storage/edits</code> or <code>/data/cache/edits</code>.</p></form>
<div class="path-box"><span>Current edited JPEG path</span><strong id="editPath">—</strong></div>
<div class="stats-grid"><div class="stat"><span>Saved JPEGs</span><strong id="editFiles">—</strong></div><div class="stat"><span>Current size</span><strong id="editBytes">—</strong></div></div>
<p id="editActionMessage" class="action-message"></p>
</section>

""" + worker_marker
replace('app/static/settings.html', worker_marker, edit_section)

print('RAW editor feature patches applied')
