from pathlib import Path


def replace_once(path, old, new):
    path = Path(path)
    text = path.read_text()
    if old not in text:
        raise RuntimeError(f'expected text not found in {path}: {old[:160]!r}')
    path.write_text(text.replace(old, new, 1))


def append_once(path, marker, content):
    path = Path(path)
    text = path.read_text()
    if marker not in text:
        path.write_text(text + content)


# ---------------------------------------------------------------------------
# Database: persistent per-photo favorites, including upgrades for live DBs.
# ---------------------------------------------------------------------------
replace_once('app/db.py',
"""    preview_error: Mapped[str | None] = mapped_column(Text, nullable=True)\n    indexed_at: Mapped[datetime] = mapped_column(DateTime, default=now)\n    __table_args__ = (Index('ix_camera_lens_id', 'camera', 'lens', 'id'),)\n""",
"""    preview_error: Mapped[str | None] = mapped_column(Text, nullable=True)\n    indexed_at: Mapped[datetime] = mapped_column(DateTime, default=now)\n    favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)\n    __table_args__ = (Index('ix_camera_lens_id', 'camera', 'lens', 'id'),)\n""")

replace_once('app/db.py',
"""        if data_type and str(data_type).lower() != 'longtext':\n            connection.execute(text('ALTER TABLE settings MODIFY value LONGTEXT NOT NULL'))\n\n\ndef init_db():\n    Base.metadata.create_all(engine)\n    _upgrade_mysql_schema()\n""",
"""        if data_type and str(data_type).lower() != 'longtext':\n            connection.execute(text('ALTER TABLE settings MODIFY value LONGTEXT NOT NULL'))\n        favorite_column = connection.scalar(text(\"\"\"\n            SELECT COUNT(*) FROM information_schema.COLUMNS\n            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'photos' AND COLUMN_NAME = 'favorite'\n        \"\"\"))\n        if not favorite_column:\n            connection.execute(text('ALTER TABLE photos ADD COLUMN favorite BOOLEAN NOT NULL DEFAULT 0'))\n        favorite_index = connection.scalar(text(\"\"\"\n            SELECT COUNT(*) FROM information_schema.STATISTICS\n            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'photos' AND INDEX_NAME = 'ix_photos_favorite'\n        \"\"\"))\n        if not favorite_index:\n            connection.execute(text('CREATE INDEX ix_photos_favorite ON photos (favorite)'))\n\n\ndef _upgrade_sqlite_schema():\n    if engine.dialect.name != 'sqlite':\n        return\n    with engine.begin() as connection:\n        columns = {row[1] for row in connection.execute(text('PRAGMA table_info(photos)'))}\n        if columns and 'favorite' not in columns:\n            connection.execute(text('ALTER TABLE photos ADD COLUMN favorite BOOLEAN NOT NULL DEFAULT 0'))\n        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_photos_favorite ON photos (favorite)'))\n\n\ndef init_db():\n    Base.metadata.create_all(engine)\n    _upgrade_mysql_schema()\n    _upgrade_sqlite_schema()\n""")

# ---------------------------------------------------------------------------
# Backend: persistent import defaults, favorites API/filter, edits gallery.
# ---------------------------------------------------------------------------
replace_once('app/web.py', 'import fcntl\nimport hmac\nimport os\nimport secrets\n',
             'import fcntl\nimport hmac\nimport json\nimport os\nimport secrets\n')
replace_once('app/web.py', 'from pathlib import Path\n', 'from pathlib import Path\nfrom urllib.parse import quote\n')
replace_once('app/web.py',
"""SCAN_PARALLELISM_PREFIX = 'scan_parallelism:'\nPARALLELISM_VALUES = (1, 2, 4, 6, 8)\n""",
"""SCAN_PARALLELISM_PREFIX = 'scan_parallelism:'\nIMPORT_DEFAULTS_KEY = 'import_defaults_v1'\nPARALLELISM_VALUES = (1, 2, 4, 6, 8)\nDEFAULT_IMPORT_OPTIONS = {\n    'parallelism': 4,\n    'skip_previews': False,\n    'skip_imported': False,\n    'force': False,\n    'skip_post_stat': False,\n}\n""")

replace_once('app/web.py',
"""def display_root(relative):\n    return '/photos' + (f'/{relative}' if relative else '')\n\n\ndef resolve_folder(relative):\n""",
"""def display_root(relative):\n    return '/photos' + (f'/{relative}' if relative else '')\n\n\ndef normalize_import_options(data, base=None):\n    if not isinstance(data, dict):\n        raise ValueError('Import options must be an object')\n    result = dict(DEFAULT_IMPORT_OPTIONS)\n    if base:\n        result.update(base)\n    if 'parallelism' in data:\n        try:\n            result['parallelism'] = int(data['parallelism'])\n        except (TypeError, ValueError) as exc:\n            raise ValueError('Parallel processing must be one of 1, 2, 4, 6 or 8') from exc\n    if result['parallelism'] not in PARALLELISM_VALUES:\n        raise ValueError('Parallel processing must be one of 1, 2, 4, 6 or 8')\n    for name in ('skip_previews', 'skip_imported', 'force', 'skip_post_stat'):\n        if name in data:\n            if not isinstance(data[name], bool):\n                raise ValueError(f'{name} must be true or false')\n            result[name] = data[name]\n    if result['force'] and result['skip_imported']:\n        raise ValueError('Force re-index and Skip already imported cannot be enabled together')\n    return result\n\n\ndef configured_import_options(db):\n    setting = db.get(Setting, IMPORT_DEFAULTS_KEY)\n    if not setting or not setting.value:\n        return dict(DEFAULT_IMPORT_OPTIONS)\n    try:\n        value = json.loads(setting.value)\n        return normalize_import_options(value)\n    except (TypeError, ValueError):\n        return dict(DEFAULT_IMPORT_OPTIONS)\n\n\ndef store_import_options(db, options):\n    encoded = json.dumps(normalize_import_options(options), separators=(',', ':'), sort_keys=True)\n    setting = db.get(Setting, IMPORT_DEFAULTS_KEY)\n    if setting is None:\n        db.add(Setting(key=IMPORT_DEFAULTS_KEY, value=encoded))\n    else:\n        setting.value = encoded\n\n\ndef resolve_folder(relative):\n""")

replace_once('app/web.py',
"""        q = request.args.get('q', '').strip()[:200]\n        if q:\n            clauses.append(or_(Photo.filename.contains(q, autoescape=True), Photo.path.contains(q, autoescape=True)))\n        start = date_arg('date_from')\n""",
"""        q = request.args.get('q', '').strip()[:200]\n        if q:\n            clauses.append(or_(Photo.filename.contains(q, autoescape=True), Photo.path.contains(q, autoescape=True)))\n        favorite = request.args.get('favorite', '').strip().lower()\n        if favorite in ('1', 'true', 'yes'):\n            clauses.append(Photo.favorite.is_(True))\n        elif favorite not in ('', '0', 'false', 'no'):\n            abort(400, 'Invalid favorite filter')\n        start = date_arg('date_from')\n""")

replace_once('app/web.py',
"""    @app.get('/api/photos/<int:photo_id>')\n    def detail(photo_id):\n        with Session() as db:\n            p = db.get(Photo, photo_id)\n            if not p:\n                abort(404, 'Photo not found')\n            return jsonify(**serialize_photo(p), path=p.path, metadata=p.metadata_json,\n                           preview_error=p.preview_error, size=p.size)\n\n    def generate_preview_on_demand(db, photo):\n""",
"""    @app.get('/api/photos/<int:photo_id>')\n    def detail(photo_id):\n        with Session() as db:\n            p = db.get(Photo, photo_id)\n            if not p:\n                abort(404, 'Photo not found')\n            return jsonify(**serialize_photo(p), path=p.path, metadata=p.metadata_json,\n                           preview_error=p.preview_error, size=p.size)\n\n    @app.put('/api/photos/<int:photo_id>/favorite')\n    def update_favorite(photo_id):\n        data = request.get_json(silent=True) or {}\n        value = data.get('favorite')\n        if not isinstance(value, bool):\n            abort(400, 'favorite must be true or false')\n        with Session.begin() as db:\n            photo = db.get(Photo, photo_id)\n            if not photo:\n                abort(404, 'Photo not found')\n            photo.favorite = value\n        return jsonify(ok=True, id=photo_id, favorite=value)\n\n    def generate_preview_on_demand(db, photo):\n""")

replace_once('app/web.py',
"""    @app.get('/media/edit/<filename>')\n    def edited_media(filename):\n        if filename != Path(filename).name or not filename.lower().endswith('.jpg'):\n            abort(404)\n        with Session() as db:\n            value = configured_edit_folder(db)\n        try:\n            root = resolve_edit_root(value, create=False)\n            path = root / filename\n            if path.is_symlink() or not path.is_file() or path.resolve(strict=True).parent != root:\n                abort(404, 'Edited JPEG not found')\n        except (ValueError, RuntimeError, OSError):\n            abort(404, 'Edited JPEG not found')\n        return send_file(path, mimetype='image/jpeg', conditional=True,\n                         as_attachment=request.args.get('download') == '1', download_name=filename)\n\n    def active_scan(db):\n""",
"""    def resolve_edited_file(filename, create_root=False):\n        if not isinstance(filename, str) or filename != Path(filename).name or not filename.lower().endswith(('.jpg', '.jpeg')):\n            abort(404, 'Edited JPEG not found')\n        with Session() as db:\n            value = configured_edit_folder(db)\n        try:\n            root = resolve_edit_root(value, create=create_root)\n            path = root / filename\n            if path.is_symlink() or not path.is_file() or path.resolve(strict=True).parent != root:\n                abort(404, 'Edited JPEG not found')\n            return root, path\n        except (ValueError, RuntimeError, OSError):\n            abort(404, 'Edited JPEG not found')\n\n    @app.get('/media/edit/<filename>')\n    def edited_media(filename):\n        _, path = resolve_edited_file(filename)\n        return send_file(path, mimetype='image/jpeg', conditional=True,\n                         as_attachment=request.args.get('download') == '1', download_name=path.name)\n\n    @app.get('/api/edits')\n    def edited_gallery():\n        with Session() as db:\n            value = configured_edit_folder(db)\n        try:\n            root = resolve_edit_root(value, create=False)\n        except (ValueError, RuntimeError) as exc:\n            abort(503, f'Edited JPEG storage is unavailable: {exc}')\n        items = []\n        if root.is_dir() and not root.is_symlink():\n            try:\n                entries = list(root.iterdir())\n            except OSError as exc:\n                abort(503, f'Edited JPEG folder could not be read: {exc}')\n            for path in entries:\n                try:\n                    if path.is_symlink() or not path.is_file() or path.suffix.lower() not in ('.jpg', '.jpeg'):\n                        continue\n                    if path.resolve(strict=True).parent != root:\n                        continue\n                    stat = path.stat()\n                    items.append(dict(filename=path.name, bytes=stat.st_size, mtime=stat.st_mtime))\n                except OSError:\n                    continue\n        items.sort(key=lambda item: (item['mtime'], item['filename']), reverse=True)\n        for item in items:\n            encoded = quote(item['filename'], safe='')\n            item['modified_at'] = datetime.utcfromtimestamp(item.pop('mtime')).isoformat() + 'Z'\n            item['url'] = f'/media/edit/{encoded}'\n            item['download_url'] = f'/media/edit/{encoded}?download=1'\n        return jsonify(folder=str(root), total=len(items), items=items)\n\n    @app.delete('/api/edits/<filename>')\n    def delete_edited_photo(filename):\n        _, path = resolve_edited_file(filename)\n        try:\n            size = path.stat().st_size\n            path.unlink()\n        except OSError as exc:\n            abort(500, f'Edited JPEG could not be deleted: {exc}')\n        return jsonify(ok=True, filename=filename, bytes_removed=size)\n\n    def active_scan(db):\n""")

replace_once('app/web.py',
"""    @app.get('/settings')\n    def settings_page():\n        return app.send_static_file('settings.html')\n\n    @app.get('/statistics')\n""",
"""    @app.get('/settings')\n    def settings_page():\n        return app.send_static_file('settings.html')\n\n    @app.get('/edits')\n    def edits_page():\n        return app.send_static_file('edits.html')\n\n    @app.get('/statistics')\n""")

replace_once('app/web.py',
"""    @app.get('/api/settings/thumbnails')\n    def thumbnail_settings():\n""",
"""    @app.get('/api/settings/import')\n    def import_settings():\n        with Session() as db:\n            return jsonify(**configured_import_options(db))\n\n    @app.put('/api/settings/import')\n    def update_import_settings():\n        data = request.get_json(silent=True) or {}\n        with Session() as db:\n            current = configured_import_options(db)\n        try:\n            options = normalize_import_options(data, current)\n        except ValueError as exc:\n            abort(400, str(exc))\n        with Session.begin() as db:\n            store_import_options(db, options)\n        return jsonify(**options)\n\n    @app.get('/api/settings/thumbnails')\n    def thumbnail_settings():\n""")

replace_once('app/web.py',
"""    @app.post('/api/scan')\n    def start_scan():\n        data = request.get_json(silent=True) or {}\n        return jsonify(scan=queue_job('scan', force=data.get('force') is True,\n                                      skip_previews=data.get('skip_previews') is True,\n                                      skip_imported=data.get('skip_imported') is True,\n                                      skip_post_stat=data.get('skip_post_stat') is True,\n                                      parallelism=data.get('parallelism', 1))), 202\n""",
"""    @app.post('/api/scan')\n    def start_scan():\n        data = request.get_json(silent=True) or {}\n        with Session() as db:\n            current = configured_import_options(db)\n        try:\n            options = normalize_import_options(data, current)\n        except ValueError as exc:\n            abort(400, str(exc))\n        with Session.begin() as db:\n            store_import_options(db, options)\n        return jsonify(scan=queue_job('scan', **options), options=options), 202\n""")

replace_once('app/web.py',
"""def serialize_photo(p):\n    return dict(id=p.id, filename=p.filename, camera=p.camera, lens=p.lens,\n                taken_at=p.taken_at.isoformat() if p.taken_at else None,\n                thumbnail=f'/media/{p.id}/thumb?v={p.cache_key}' if p.cache_key else None,\n                preview=f'/media/{p.id}/preview?v={p.cache_key}' if p.cache_key else None)\n""",
"""def serialize_photo(p):\n    return dict(id=p.id, filename=p.filename, camera=p.camera, lens=p.lens,\n                taken_at=p.taken_at.isoformat() if p.taken_at else None, favorite=bool(p.favorite),\n                thumbnail=f'/media/{p.id}/thumb?v={p.cache_key}' if p.cache_key else None,\n                preview=f'/media/{p.id}/preview?v={p.cache_key}' if p.cache_key else None)\n""")

# ---------------------------------------------------------------------------
# Indexer: restore and auto-save all import behavior options.
# ---------------------------------------------------------------------------
Path('app/static/indexer.js').write_text(r'''\
'use strict';
const $ = id => document.getElementById(id);
const nf = new Intl.NumberFormat();
let csrf = '', timer, browsePath = '', authenticated = false, importSaveTimer;

async function api(url, options = {}) {
  const response = await fetch(url, {...options, headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...(options.headers||{})}});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) location.href = '/';
    throw new Error(data.error || `Request failed (${response.status})`);
  }
  return data;
}
function showError(err){$('indexerError').textContent=err.message;$('indexerError').hidden=false;}
function clearError(){$('indexerError').hidden=true;}
function syncOptions(){
  if ($('skipImported').checked) {$('force').checked=false;$('force').disabled=true;} else $('force').disabled=false;
  if ($('force').checked) {$('skipImported').checked=false;$('skipImported').disabled=true;} else $('skipImported').disabled=false;
}
function importOptions(){return {parallelism:Number($('parallelism').value),skip_previews:$('skipPreviews').checked,skip_imported:$('skipImported').checked,force:$('force').checked,skip_post_stat:$('skipPostStat').checked};}
function applyImportOptions(data){$('parallelism').value=String(data.parallelism||4);$('skipPreviews').checked=!!data.skip_previews;$('skipImported').checked=!!data.skip_imported;$('force').checked=!!data.force;$('skipPostStat').checked=!!data.skip_post_stat;syncOptions();}
async function saveImportOptions(){if(!authenticated)return;try{const data=await api('/api/settings/import',{method:'PUT',body:JSON.stringify(importOptions())});applyImportOptions(data);$('importDefaultsState').textContent='Saved for subsequent imports.';}catch(err){showError(err);$('importDefaultsState').textContent='Could not save import defaults.';}}
function scheduleImportSave(){syncOptions();clearTimeout(importSaveTimer);$('importDefaultsState').textContent='Saving defaults…';importSaveTimer=setTimeout(saveImportOptions,250);}
async function loadImportOptions(){applyImportOptions(await api('/api/settings/import'));$('importDefaultsState').textContent='These options are persistent and will be reused for subsequent imports.';}
function setBusy(busy){
  for(const id of ['browseFolder','parallelism','skipPreviews','skipImported','force','skipPostStat','startScan']) $(id).disabled=busy;
  if(!busy) syncOptions();
}
async function loadFolders(path=''){
  const data=await api('/api/folders?path='+encodeURIComponent(path));
  browsePath=data.current;$('folderCurrent').textContent=data.current_display;
  $('folderUp').disabled=data.parent===null;$('folderUp').dataset.path=data.parent??'';
  $('folderList').replaceChildren();
  if(!data.directories.length){const e=document.createElement('span');e.className='folder-empty';e.textContent='No subfolders';$('folderList').append(e);}
  for(const folder of data.directories){const b=document.createElement('button');b.className='folder-entry';b.textContent=folder.name;b.onclick=()=>loadFolders(folder.path).catch(showError);$('folderList').append(b);}
  return data;
}
async function poll(){
  if(!authenticated)return;
  try{
    const data=await api('/api/scan'),job=data.scan,active=job&&['queued','running'].includes(job.state);
    $('selectedFolder').textContent=data.root;$('selectedFolder').dataset.path=data.selected||'';
    $('scanState').textContent=job?job.state:'Ready';
    setBusy(!!active);
    $('cancelScan').hidden=!active;$('cancelScan').disabled=!!job?.cancel;$('cancelScan').textContent=job?.cancel?'Cancelling…':'Cancel current job';
    $('scanMessage').textContent=job?(job.message||job.state):'No active indexing job.';
    $('foundCount').textContent=nf.format(job?.discovered||0);$('indexedCount').textContent=nf.format(job?.indexed||0);$('skippedCount').textContent=nf.format(job?.skipped||0);$('errorCount').textContent=nf.format(job?.errors||0);$('scanPath').textContent=job?.current_path||'';
  }catch(err){showError(err);}finally{if(authenticated)timer=setTimeout(poll,3000);}
}
for(const id of ['parallelism','skipPreviews','skipImported','force','skipPostStat']) $(id).addEventListener('change',scheduleImportSave);
$('browseFolder').onclick=async()=>{$('folderBrowser').hidden=false;try{await loadFolders($('selectedFolder').dataset.path||'');}catch(err){try{await loadFolders('');}catch(rootErr){showError(rootErr);$('folderBrowser').hidden=true;}}};
$('closeFolderBrowser').onclick=()=>{$('folderBrowser').hidden=true;};
$('folderUp').onclick=()=>loadFolders($('folderUp').dataset.path||'').catch(showError);
$('selectFolder').onclick=async()=>{clearError();$('selectFolder').disabled=true;try{const data=await api('/api/folders/select',{method:'POST',body:JSON.stringify({path:browsePath})});$('selectedFolder').textContent=data.selected_display;$('selectedFolder').dataset.path=data.selected;$('folderBrowser').hidden=true;}catch(err){showError(err);}finally{$('selectFolder').disabled=false;}};
$('startScan').onclick=async()=>{clearError();clearTimeout(importSaveTimer);$('startScan').disabled=true;try{const data=await api('/api/scan',{method:'POST',body:JSON.stringify(importOptions())});if(data.options)applyImportOptions(data.options);$('importDefaultsState').textContent='Saved for subsequent imports.';clearTimeout(timer);await poll();}catch(err){showError(err);$('startScan').disabled=false;}};
$('cancelScan').onclick=async()=>{clearError();try{await api('/api/scan/cancel',{method:'POST'});clearTimeout(timer);await poll();}catch(err){showError(err);}};
$('signOut').onclick=async()=>{try{await api('/api/logout',{method:'POST'});}finally{location.href='/';}};
(async()=>{try{const state=await api('/api/session');csrf=state.csrf;if(!state.authenticated){location.href='/';return;}authenticated=true;$('indexerApp').hidden=false;await loadImportOptions();poll();}catch(err){showError(err);$('indexerApp').hidden=false;}})();
'''.lstrip('\\'))

replace_once('app/static/indexer.html',
"""<div class=\"identity-note\"><strong>Photo identity</strong><span>Already imported currently means the exact source path. The catalog stores a SHA-256 hash of that path; moving or copying the same RAW to a different path creates another catalog entry.</span></div>\n<div class=\"start-row\"><button id=\"startScan\" class=\"primary\">Start indexing</button></div>\n""",
"""<div class=\"identity-note\"><strong>Persistent import options</strong><span id=\"importDefaultsState\">Loading saved defaults…</span></div>\n<div class=\"identity-note\"><strong>Photo identity</strong><span>Already imported currently means the exact source path. The catalog stores a SHA-256 hash of that path; moving or copying the same RAW to a different path creates another catalog entry.</span></div>\n<div class=\"start-row\"><button id=\"startScan\" class=\"primary\">Start indexing</button></div>\n""")

# ---------------------------------------------------------------------------
# Library: favorite toggle on cards/viewer plus Favorites-only filter.
# ---------------------------------------------------------------------------
Path('app/static/app.js').write_text(r'''\
'use strict';
const $ = id => document.getElementById(id);
let csrf='',items=[],cursor=null,total=0,viewing=-1,revision=0,detailRevision=0,loading=false,authenticated=false;
const nf=new Intl.NumberFormat();
async function api(url,options={}){const response=await fetch(url,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...(options.headers||{})}});const result=await response.json();if(!response.ok){if(response.status===401&&!url.endsWith('/login'))showLogin();throw new Error(result.error||`Request failed (${response.status})`);}return result;}
function error(err){$('error').textContent=err.message;$('error').hidden=false;}
function showLogin(){authenticated=false;$('application').hidden=true;$('login').hidden=false;if(window.rawCatalogEditor)window.rawCatalogEditor.close();if($('viewer').open)$('viewer').close();}
function applyUrlFilters(){const p=new URLSearchParams(location.search);for(const[param,id]of[['camera','camera'],['lens','lens']]){const value=p.get(param);if(value){const select=$(id);select.add(new Option(value,value));select.value=value;}}for(const[param,id]of[['q','query'],['date_from','dateFrom'],['date_to','dateTo'],['sort','sort']]){const value=p.get(param);if(value)$(id).value=value;}if(['1','true','yes'].includes((p.get('favorite')||'').toLowerCase()))$('favoritesOnly').checked=true;}
function params(){const p=new URLSearchParams();for(const[name,id]of[['camera','camera'],['lens','lens'],['q','query'],['date_from','dateFrom'],['date_to','dateTo'],['sort','sort']])if($(id).value&&!(name==='sort'&&$(id).value==='indexed_desc'))p.set(name,$(id).value);if($('favoritesOnly').checked)p.set('favorite','1');return p;}
function optionList(id,values){const select=$(id),selected=select.value;select.replaceChildren(new Option(id==='camera'?'All cameras':'All lenses',''));for(const item of values)select.add(new Option(`${item.value} (${nf.format(item.count)})`,item.value));if(selected&&!values.some(v=>v.value===selected))select.add(new Option(`${selected} (0)`,selected));select.value=selected;}
function favoriteLabel(photo,compact=false){return photo.favorite?(compact?'★':'★ Favorite'):(compact?'☆':'☆ Favorite');}
function updateFavoriteControls(photo){for(const control of document.querySelectorAll(`[data-favorite-id="${photo.id}"]`)){control.textContent=favoriteLabel(photo,control.classList.contains('favorite-button'));control.classList.toggle('active',!!photo.favorite);control.setAttribute('aria-label',photo.favorite?`Remove ${photo.filename} from favorites`:`Add ${photo.filename} to favorites`);control.title=photo.favorite?'Remove from favorites':'Add to favorites';}if(viewing>=0&&items[viewing]?.id===photo.id){$('viewerFavorite').textContent=favoriteLabel(photo);$('viewerFavorite').classList.toggle('active',!!photo.favorite);}}
async function toggleFavorite(photo,control){if(control)control.disabled=true;try{const result=await api(`/api/photos/${photo.id}/favorite`,{method:'PUT',body:JSON.stringify({favorite:!photo.favorite})});photo.favorite=result.favorite;updateFavoriteControls(photo);if($('favoritesOnly').checked&&!photo.favorite)await search();}catch(err){error(err);}finally{if(control&&document.body.contains(control))control.disabled=false;}}
function card(photo,index){const item=document.createElement('article');item.className='photo';item.dataset.photoId=String(photo.id);const open=document.createElement('button');open.type='button';open.className='photo-open';open.setAttribute('aria-label',`Preview ${photo.filename}, ${photo.camera}, ${photo.lens}`);const wrap=document.createElement('div');wrap.className='image-wrap';const missing=()=>{const msg=document.createElement('span');msg.className='missing';msg.textContent='Thumbnail unavailable';wrap.replaceChildren(msg);};if(photo.thumbnail){const img=document.createElement('img');img.src=photo.thumbnail;img.loading='lazy';img.decoding='async';img.alt=photo.filename;img.onerror=missing;wrap.append(img);}else missing();const info=document.createElement('div');info.className='photo-info';for(const[tag,text,cls]of[['strong',photo.filename,''],['span',photo.camera,''],['span',photo.lens,''],['span',photo.taken_at?photo.taken_at.slice(0,10):'Capture date unavailable','date']]){const e=document.createElement(tag);e.textContent=text;e.title=text;e.className=cls;info.append(e);}open.append(wrap,info);open.onclick=()=>view(index);const favorite=document.createElement('button');favorite.type='button';favorite.className='favorite-button';favorite.dataset.favoriteId=String(photo.id);favorite.onclick=e=>{e.stopPropagation();toggleFavorite(photo,favorite);};item.append(open,favorite);updateFavoriteControls(photo);return item;}
async function search(append=false){if(append&&(loading||cursor===null))return false;const rev=append?revision:++revision;loading=true;$('loadMore').disabled=true;$('error').hidden=true;const p=params();if(append)p.set('after',cursor);if(!append)$('summary').textContent='Searching photographs…';try{const[data,facets]=await Promise.all([api('/api/photos?'+p),append?null:api('/api/facets?'+p)]);if(rev!==revision)return false;if(!append){items=[];$('grid').replaceChildren();}const start=items.length;items.push(...data.items);cursor=data.next_cursor;total=data.total;data.items.forEach((photo,i)=>$('grid').append(card(photo,start+i)));if(facets){optionList('camera',facets.camera);optionList('lens',facets.lens);}$('count').textContent=nf.format(total);$('summary').textContent=`${nf.format(items.length)} of ${nf.format(total)} photographs`;const labels={indexed_desc:'Newest indexed first',date_desc:'Newest capture date first',date_asc:'Oldest capture date first'};$('sortSummary').textContent=labels[data.sort]||labels.indexed_desc;$('loadMore').hidden=cursor===null;$('empty').hidden=items.length>0;const filtered=['camera','lens','query','dateFrom','dateTo'].some(id=>$(id).value)||$('favoritesOnly').checked;$('emptyTitle').textContent=filtered?'No matching photographs':'Your archive starts here';$('emptyText').textContent=filtered?'Try different filters or a wider capture-date range.':'Open Indexer to scan your configured photo folder.';return true;}catch(err){if(rev===revision){error(err);$('summary').textContent='Could not load photographs';}return false;}finally{if(rev===revision){loading=false;$('loadMore').disabled=false;}}}
async function view(index){if(index<0)return;if(index>=items.length&&cursor!==null)await search(true);if(index>=items.length)return;viewing=index;const photo=items[index],rev=++detailRevision;$('viewerFavorite').dataset.favoriteId=String(photo.id);updateFavoriteControls(photo);$('downloadPreview').hidden=!photo.preview;if(photo.preview){$('downloadPreview').href=`/media/${photo.id}/preview?download=1`;$('downloadPreview').download=`${photo.filename.replace(/\.[^.]+$/,'')}-preview.jpg`;}$('downloadRaw').href=`/media/${photo.id}/original?download=1`;$('downloadRaw').download=photo.filename;$('editRaw').onclick=()=>window.rawCatalogEditor?.open(photo,csrf);$('viewerTitle').textContent=photo.filename;$('viewerPosition').textContent=`${index+1} of ${nf.format(total)} matching photographs`;$('viewerCamera').textContent=photo.camera;$('viewerLens').textContent=photo.lens;$('viewerPath').textContent='';$('metadata').textContent='';$('exposure').textContent='';$('previous').disabled=index===0;$('next').disabled=index===items.length-1&&cursor===null;$('previewImage').hidden=true;$('previewImage').removeAttribute('src');$('previewMessage').hidden=false;$('previewMessage').textContent=photo.preview?'Loading preview… missing previews are generated automatically.':'Preview unavailable.';$('previewImage').onload=()=>{if(rev!==detailRevision)return;$('previewImage').hidden=false;$('previewMessage').hidden=true;};$('previewImage').onerror=()=>{if(rev!==detailRevision)return;$('previewImage').hidden=true;$('previewMessage').hidden=false;$('previewMessage').textContent='Preview could not be generated or loaded. Check file details and server logs.';};if(photo.preview){$('previewImage').alt=photo.filename;$('previewImage').src=photo.preview;}if(!$('viewer').open)$('viewer').showModal();try{const detail=await api(`/api/photos/${photo.id}`);if(rev!==detailRevision)return;photo.favorite=!!detail.favorite;updateFavoriteControls(photo);$('viewerPath').textContent=detail.path;$('metadata').textContent=JSON.stringify(detail.metadata,null,2);if(detail.preview_error)$('metadata').textContent=`Preview error: ${detail.preview_error}\n\n`+$('metadata').textContent;const m=detail.metadata||{};$('exposure').textContent=[m.FocalLength,m.FNumber?`f/${m.FNumber}`:null,m.ExposureTime?`${m.ExposureTime} s`:null,m.ISO?`ISO ${m.ISO}`:null].filter(Boolean).join(' · ');}catch(err){if(rev===detailRevision)$('metadata').textContent=err.message;}}
async function enter(){authenticated=true;$('login').hidden=true;$('application').hidden=false;await search();}
$('loginForm').onsubmit=async e=>{e.preventDefault();$('loginError').textContent='';try{const state=await api('/api/session');csrf=state.csrf;const result=await api('/api/login',{method:'POST',body:JSON.stringify({password:$('password').value})});csrf=result.csrf;$('password').value='';await enter();}catch(err){$('loginError').textContent=err.message;}};
$('signOut').onclick=async()=>{try{await api('/api/logout',{method:'POST'});showLogin();}catch(err){error(err);}};
$('filters').onsubmit=e=>{e.preventDefault();search();};for(const id of['camera','lens','dateFrom','dateTo','sort','favoritesOnly'])$(id).onchange=()=>search();let debounce;$('query').oninput=()=>{clearTimeout(debounce);debounce=setTimeout(()=>search(),300);};$('reset').onclick=()=>{clearTimeout(debounce);for(const id of['camera','lens','query','dateFrom','dateTo'])$(id).value='';$('favoritesOnly').checked=false;$('sort').value='indexed_desc';history.replaceState(null,'',location.pathname);search();};$('loadMore').onclick=()=>search(true);
$('viewerFavorite').onclick=()=>{if(viewing>=0&&items[viewing])toggleFavorite(items[viewing],$('viewerFavorite'));};
function syncFullScreenButton(){const active=document.fullscreenElement===$('previewStage');$('fullScreen').textContent=active?'Exit full screen':'Full screen';$('fullScreen').title=active?'Exit full screen':'Enter full screen';}
$('closeViewer').onclick=()=>$('viewer').close();$('viewer').addEventListener('close',()=>{detailRevision++;if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});});$('previous').onclick=()=>view(viewing-1);$('next').onclick=()=>view(viewing+1);$('fullScreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await $('previewStage').requestFullscreen();}catch(err){$('metadata').textContent=`Full screen error: ${err.message}\n\n`+$('metadata').textContent;}};if(!$('previewStage').requestFullscreen)$('fullScreen').hidden=true;document.addEventListener('fullscreenchange',syncFullScreenButton);document.addEventListener('keydown',e=>{if(!$('viewer').open)return;if(e.key==='ArrowRight'){e.preventDefault();view(viewing+1);}if(e.key==='ArrowLeft'){e.preventDefault();view(viewing-1);}});
applyUrlFilters();
(async()=>{try{const state=await api('/api/session');csrf=state.csrf;if(state.authenticated)await enter();else showLogin();}catch(err){showLogin();$('loginError').textContent=err.message;}})();
'''.lstrip('\\'))

replace_once('app/static/index.html',
"""<header><div class=\"brand\"><span class=\"mark\">R / C</span><div><strong>RAW Catalog</strong><span>Photo archive</span></div></div><div class=\"header-actions\"><a class=\"button-link\" href=\"/indexer\">Indexer</a><a class=\"button-link\" href=\"/statistics\">Statistics</a><a class=\"button-link\" href=\"/settings\">Settings</a><button id=\"signOut\">Sign out</button></div></header>\n""",
"""<header><div class=\"brand\"><span class=\"mark\">R / C</span><div><strong>RAW Catalog</strong><span>Photo archive</span></div></div><div class=\"header-actions\"><a class=\"button-link\" href=\"/edits\">Edits</a><a class=\"button-link\" href=\"/indexer\">Indexer</a><a class=\"button-link\" href=\"/statistics\">Statistics</a><a class=\"button-link\" href=\"/settings\">Settings</a><button id=\"signOut\">Sign out</button></div></header>\n""")
replace_once('app/static/index.html',
"""<div><label for=\"sort\">Sort</label><select id=\"sort\"><option value=\"indexed_desc\">Newest indexed</option><option value=\"date_desc\">Capture date — newest</option><option value=\"date_asc\">Capture date — oldest</option></select></div>\n<button type=\"button\" id=\"reset\">Clear filters</button>\n""",
"""<div><label for=\"sort\">Sort</label><select id=\"sort\"><option value=\"indexed_desc\">Newest indexed</option><option value=\"date_desc\">Capture date — newest</option><option value=\"date_asc\">Capture date — oldest</option></select></div>\n<label class=\"favorite-filter\" for=\"favoritesOnly\"><input id=\"favoritesOnly\" type=\"checkbox\"><span>Favorites only</span></label>\n<button type=\"button\" id=\"reset\">Clear filters</button>\n""")
replace_once('app/static/index.html',
"""<div class=\"viewer-actions\"><a id=\"downloadPreview\" class=\"button-link\" href=\"#\">Preview JPEG</a><a id=\"downloadRaw\" class=\"button-link\" href=\"#\">Original RAW</a><button id=\"editRaw\">RAW Editor</button><button id=\"fullScreen\" title=\"Enter full screen\">Full screen</button><button id=\"closeViewer\" aria-label=\"Close preview\">Close ×</button></div>\n""",
"""<div class=\"viewer-actions\"><button id=\"viewerFavorite\">☆ Favorite</button><a id=\"downloadPreview\" class=\"button-link\" href=\"#\">Preview JPEG</a><a id=\"downloadRaw\" class=\"button-link\" href=\"#\">Original RAW</a><button id=\"editRaw\">RAW Editor</button><button id=\"fullScreen\" title=\"Enter full screen\">Full screen</button><button id=\"closeViewer\" aria-label=\"Close preview\">Close ×</button></div>\n""")

append_once('app/static/style.css', '.favorite-button{', r'''
.photo{position:relative}.photo-open{display:block;width:100%;padding:0;border:0;border-radius:0;background:transparent;color:inherit;text-align:left}.photo-open:hover{background:#23262b}.favorite-button{position:absolute;z-index:2;right:.55rem;top:.55rem;width:2.15rem;height:2.15rem;padding:0;border-radius:50%;display:grid;place-items:center;font-size:1.2rem;background:#101114d9;border-color:#555963;box-shadow:0 2px 10px #0008}.favorite-button:hover{background:#292c33}.favorite-button.active,#viewerFavorite.active{color:#f4d35e;border-color:#8a7b3f}.favorite-filter{display:flex;align-items:center;gap:.5rem;flex:0 0 auto;margin:0;padding:.7rem .8rem;border:1px solid #41434c;border-radius:6px;background:#111216;color:#e9eaf0;white-space:nowrap}.favorite-filter input{width:auto;margin:0}.viewer-actions{display:flex;align-items:center;gap:.45rem;flex-wrap:wrap}
''')

# ---------------------------------------------------------------------------
# Saved-edits gallery: view, download again, or delete.
# ---------------------------------------------------------------------------
replace_once('app/static/settings.html',
"""<header><div class=\"brand\"><span class=\"mark\">R / C</span><div><strong>RAW Catalog</strong><span>Settings</span></div></div><div class=\"header-actions\"><a class=\"button-link\" href=\"/\">Library</a><a class=\"button-link\" href=\"/indexer\">Indexer</a><a class=\"button-link\" href=\"/statistics\">Statistics</a><button id=\"signOut\">Sign out</button></div></header>\n""",
"""<header><div class=\"brand\"><span class=\"mark\">R / C</span><div><strong>RAW Catalog</strong><span>Settings</span></div></div><div class=\"header-actions\"><a class=\"button-link\" href=\"/\">Library</a><a class=\"button-link\" href=\"/edits\">Edits</a><a class=\"button-link\" href=\"/indexer\">Indexer</a><a class=\"button-link\" href=\"/statistics\">Statistics</a><button id=\"signOut\">Sign out</button></div></header>\n""")
replace_once('app/static/settings.html',
"""<div class=\"stats-grid\"><div class=\"stat\"><span>Saved JPEGs</span><strong id=\"editFiles\">—</strong></div><div class=\"stat\"><span>Current size</span><strong id=\"editBytes\">—</strong></div></div>\n<p id=\"editActionMessage\" class=\"action-message\"></p>\n""",
"""<div class=\"stats-grid\"><div class=\"stat\"><span>Saved JPEGs</span><strong id=\"editFiles\">—</strong></div><div class=\"stat\"><span>Current size</span><strong id=\"editBytes\">—</strong></div></div>\n<div class=\"danger-row\"><div><strong>Saved edits gallery</strong><span>View JPEGs saved by the RAW editor, download them again, or delete individual files.</span></div><a class=\"button-link\" href=\"/edits\">Browse saved edits</a></div>\n<p id=\"editActionMessage\" class=\"action-message\"></p>\n""")

Path('app/static/edits.html').write_text('''<!doctype html>\n<html lang="en">\n<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><title>RAW Catalog Edits</title><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/controls.css"><link rel="stylesheet" href="/static/edits.css"><script defer src="/static/edits.js"></script></head>\n<body>\n<div id="editsApp" hidden>\n<header><div class="brand"><span class="mark">R / C</span><div><strong>RAW Catalog</strong><span>Saved edits</span></div></div><div class="header-actions"><a class="button-link" href="/">Library</a><a class="button-link" href="/indexer">Indexer</a><a class="button-link" href="/statistics">Statistics</a><a class="button-link" href="/settings">Settings</a><button id="signOut">Sign out</button></div></header>\n<main class="edits-main"><div class="title-row"><div><p class="eyebrow">EDITED JPEGS</p><h1>Saved RAW editor output<span id="editCount">—</span></h1></div><button id="refreshEdits">Refresh</button></div><p id="editFolder" class="edits-folder"></p><div id="editsError" class="error" role="alert" hidden></div><div id="editsLoading" class="empty"><h2>Loading saved edits…</h2></div><section id="editsGrid" class="edits-grid"></section><div id="editsEmpty" class="empty" hidden><h2>No saved edits</h2><p>Save a full-resolution JPEG from the RAW editor and it will appear here.</p></div></main>\n<footer>RAW Catalog <span>Saved JPEGs are separate from the read-only RAW archive</span></footer>\n</div>\n<dialog id="editViewer"><div class="saved-viewer"><div class="viewer-top"><strong id="savedViewerTitle"></strong><button id="closeSavedViewer">Close ×</button></div><div class="saved-stage"><img id="savedViewerImage" alt="Saved edited JPEG"></div></div></dialog>\n</body></html>\n''')
Path('app/static/edits.js').write_text(r'''\
'use strict';
const $=id=>document.getElementById(id);let csrf='';const nf=new Intl.NumberFormat();
async function api(url,options={}){const response=await fetch(url,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...(options.headers||{})}});const data=await response.json();if(!response.ok){if(response.status===401)location.href='/';throw new Error(data.error||`Request failed (${response.status})`);}return data;}
function formatBytes(value){if(!value)return'0 B';const units=['B','KB','MB','GB','TB'];const i=Math.min(units.length-1,Math.floor(Math.log(value)/Math.log(1024)));return`${(value/(1024**i)).toFixed(i?1:0)} ${units[i]}`;}
function fail(err){$('editsError').textContent=err.message;$('editsError').hidden=false;$('editsLoading').hidden=true;}
function openViewer(item){$('savedViewerTitle').textContent=item.filename;$('savedViewerImage').src=item.url;if(!$('editViewer').open)$('editViewer').showModal();}
function card(item){const article=document.createElement('article');article.className='edit-card';const imageButton=document.createElement('button');imageButton.className='edit-image';imageButton.type='button';const img=document.createElement('img');img.src=item.url;img.loading='lazy';img.decoding='async';img.alt=item.filename;imageButton.append(img);imageButton.onclick=()=>openViewer(item);const body=document.createElement('div');body.className='edit-card-body';const title=document.createElement('strong');title.textContent=item.filename;title.title=item.filename;const meta=document.createElement('span');meta.textContent=`${formatBytes(item.bytes)} · ${new Date(item.modified_at).toLocaleString()}`;const actions=document.createElement('div');actions.className='edit-card-actions';const download=document.createElement('a');download.className='button-link';download.href=item.download_url;download.download=item.filename;download.textContent='Download';const remove=document.createElement('button');remove.textContent='Delete';remove.onclick=async()=>{if(!confirm(`Delete saved edit ${item.filename}?`))return;remove.disabled=true;try{await api('/api/edits/'+encodeURIComponent(item.filename),{method:'DELETE'});await load();}catch(err){fail(err);remove.disabled=false;}};actions.append(download,remove);body.append(title,meta,actions);article.append(imageButton,body);return article;}
async function load(){$('editsError').hidden=true;$('editsLoading').hidden=false;try{const data=await api('/api/edits');$('editFolder').textContent=`Current editor folder: ${data.folder}`;$('editCount').textContent=nf.format(data.total);$('editsGrid').replaceChildren(...data.items.map(card));$('editsEmpty').hidden=data.items.length>0;$('editsLoading').hidden=true;}catch(err){fail(err);}}
$('refreshEdits').onclick=load;$('closeSavedViewer').onclick=()=>$('editViewer').close();$('editViewer').addEventListener('close',()=>{$('savedViewerImage').removeAttribute('src');});$('signOut').onclick=async()=>{try{await api('/api/logout',{method:'POST'});}finally{location.href='/';}};
(async()=>{try{const state=await api('/api/session');csrf=state.csrf;if(!state.authenticated){location.href='/';return;}$('editsApp').hidden=false;await load();}catch(err){$('editsApp').hidden=false;fail(err);}})();
'''.lstrip('\\'))
Path('app/static/edits.css').write_text('''.edits-main{max-width:1800px}.edits-folder{color:var(--muted);font-size:.8rem;margin:-1rem 0 1.5rem;overflow-wrap:anywhere}.edits-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:1.1rem}.edit-card{background:#191a20;border:1px solid var(--line);border-radius:9px;overflow:hidden;min-width:0}.edit-image{display:block;width:100%;padding:0;border:0;border-radius:0;background:#050608;aspect-ratio:3/2;overflow:hidden}.edit-image img{display:block;width:100%;height:100%;object-fit:contain}.edit-card-body{padding:.85rem}.edit-card-body strong,.edit-card-body span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.edit-card-body strong{font-size:.86rem}.edit-card-body span{color:var(--muted);font-size:.74rem;margin:.45rem 0 .7rem}.edit-card-actions{display:flex;gap:.5rem}.edit-card-actions>*{flex:1;text-align:center}.saved-viewer{height:100%;display:flex;flex-direction:column}.saved-viewer .viewer-top{flex:0 0 auto}.saved-stage{flex:1;min-height:0;background:#000;display:grid;place-items:center}.saved-stage img{width:100%;height:100%;object-fit:contain}@media(max-width:520px){.edits-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:.6rem}.edit-card-actions{flex-direction:column}}\n''')

# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------
append_once('tests/test_preview_on_demand.py', 'def test_import_options_are_persistent', r'''


def test_import_options_are_persistent(client):
    headers = {'X-CSRF-Token': client.csrf}
    initial = client.get('/api/settings/import')
    assert initial.status_code == 200
    assert initial.json['parallelism'] == 4
    saved = client.put('/api/settings/import', json={
        'parallelism': 6, 'skip_previews': True, 'skip_imported': True,
        'force': False, 'skip_post_stat': True,
    }, headers=headers)
    assert saved.status_code == 200
    assert saved.json['parallelism'] == 6 and saved.json['skip_post_stat'] is True
    again = client.get('/api/settings/import').json
    assert again == saved.json
    queued = client.post('/api/scan', json={}, headers=headers)
    assert queued.status_code == 202
    job_id = queued.json['scan']['id']
    with Session() as db:
        assert db.get(Setting, f'scan_parallelism:{job_id}').value == '6'
        assert db.get(Setting, f'scan_skip_previews:{job_id}').value == '1'
        assert db.get(Setting, f'scan_skip_imported:{job_id}').value == '1'
        assert db.get(Setting, f'scan_skip_post_stat:{job_id}').value == '1'


def test_favorite_toggle_and_filter(client):
    headers = {'X-CSRF-Token': client.csrf}
    with Session.begin() as db:
        first = Photo(path_hash='favorite-a', path='/photos/a.cr3', filename='a.cr3', size=1, mtime_ns=1,
                      camera='Canon', lens='Lens A', metadata_json={})
        second = Photo(path_hash='favorite-b', path='/photos/b.cr3', filename='b.cr3', size=1, mtime_ns=1,
                       camera='Canon', lens='Lens B', metadata_json={})
        db.add_all([first, second]); db.flush(); first_id = first.id
    changed = client.put(f'/api/photos/{first_id}/favorite', json={'favorite': True}, headers=headers)
    assert changed.status_code == 200 and changed.json['favorite'] is True
    filtered = client.get('/api/photos?favorite=1')
    assert filtered.status_code == 200
    assert filtered.json['total'] == 1
    assert filtered.json['items'][0]['id'] == first_id
    assert filtered.json['items'][0]['favorite'] is True
    with Session() as db:
        assert db.get(Photo, first_id).favorite is True


def test_saved_edits_gallery_download_and_delete(client, tmp_path, monkeypatch):
    cache = tmp_path / 'cache'; storage = tmp_path / 'storage'; storage.mkdir()
    edit_root = storage / 'edits'; edit_root.mkdir()
    monkeypatch.setenv('CACHE_DIR', str(cache))
    monkeypatch.setenv('EXTERNAL_STORAGE_ROOT', str(storage))
    with Session.begin() as db:
        db.add(Setting(key='edit_folder', value=str(edit_root)))
    saved = edit_root / 'sample edit.jpg'
    saved.write_bytes(b'jpeg-data')
    listing = client.get('/api/edits')
    assert listing.status_code == 200 and listing.json['total'] == 1
    item = listing.json['items'][0]
    assert item['filename'] == saved.name
    downloaded = client.get(item['download_url'])
    assert downloaded.status_code == 200 and downloaded.data == b'jpeg-data'
    deleted = client.delete('/api/edits/sample%20edit.jpg', headers={'X-CSRF-Token': client.csrf})
    assert deleted.status_code == 200
    assert not saved.exists()
''')
