from pathlib import Path


def replace_once(path, old, new):
    path = Path(path)
    text = path.read_text()
    if old not in text:
        raise RuntimeError(f'expected text not found in {path}: {old[:120]!r}')
    path.write_text(text.replace(old, new, 1))


# --- RAW editor working-preview cache ---
replace_once('app/editor.py',
'''import io
import math
from pathlib import Path
''',
'''import fcntl
import hashlib
import io
import math
import os
from pathlib import Path
''')

replace_once('app/editor.py',
'''def _decode_raw(path, denoise=0, half_size=False):
    passes = min(3, max(0, int(round(denoise / 34.0))))
    with rawpy.imread(str(path)) as raw:
        pixels = raw.postprocess(
            use_camera_wb=True,
            half_size=half_size,
            output_color=rawpy.ColorSpace.sRGB,
            output_bps=8,
            median_filter_passes=passes,
        )
    return Image.fromarray(pixels, 'RGB')


def render(path, settings, max_edge=None):
    settings = normalize_settings(settings)
    image = _decode_raw(path, settings['denoise'], half_size=bool(max_edge))
    try:
        toned = _tone_image(image, settings)
    finally:
        image.close()
    if max_edge:
        toned.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return toned
''',
'''def _denoise_passes(denoise):
    return min(3, max(0, int(round(float(denoise) / 34.0))))


def _decode_raw(path, denoise=0, half_size=False):
    passes = _denoise_passes(denoise)
    with rawpy.imread(str(path)) as raw:
        pixels = raw.postprocess(
            use_camera_wb=True,
            half_size=half_size,
            output_color=rawpy.ColorSpace.sRGB,
            output_bps=8,
            median_filter_passes=passes,
        )
    return Image.fromarray(pixels, 'RGB')


def _editor_work_root():
    root = Path(os.environ.get('CACHE_DIR', '/data/cache')) / 'editor-work'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _preview_cache_file(path, denoise, max_edge):
    source = Path(path)
    stat = source.stat()
    signature = f'{source}:{stat.st_size}:{stat.st_mtime_ns}:{_denoise_passes(denoise)}:{int(max_edge)}'
    key = hashlib.sha256(signature.encode('utf-8', errors='surrogateescape')).hexdigest()
    return _editor_work_root() / key[:2] / f'{key}.jpg'


def _open_cached_rgb(path):
    with Image.open(path) as image:
        return image.convert('RGB')


def _load_cached_preview_base(path, denoise=0, max_edge=1600):
    """Decode the RAW once per source/denoise bucket and reuse a local working JPEG."""
    target = _preview_cache_file(path, denoise, max_edge)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        return _open_cached_rgb(target)

    lock_path = target.with_suffix('.lock')
    with open(lock_path, 'a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not target.is_file():
            image = _decode_raw(path, denoise, half_size=True)
            temp = target.with_suffix(f'.{os.getpid()}.tmp')
            try:
                image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                image.save(temp, 'JPEG', quality=92, optimize=False)
                os.replace(temp, target)
            finally:
                image.close()
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass
    return _open_cached_rgb(target)


def render(path, settings, max_edge=None):
    settings = normalize_settings(settings)
    image = (_load_cached_preview_base(path, settings['denoise'], max_edge)
             if max_edge else _decode_raw(path, settings['denoise'], half_size=False))
    try:
        toned = _tone_image(image, settings)
    finally:
        image.close()
    return toned
''')

replace_once('app/editor.py',
'''    image = _decode_raw(path, denoise=0, half_size=True)
    try:
        image.thumbnail((800, 800), Image.Resampling.BILINEAR)
''',
'''    image = _load_cached_preview_base(path, denoise=0, max_edge=1600)
    try:
        image.thumbnail((800, 800), Image.Resampling.BILINEAR)
''')

# Keep the currently displayed preview while a cached update is rendered, cancel stale browser requests,
# and only swap the blob after the new JPEG has decoded.
replace_once('app/static/editor.js',
'''  let photo = null, csrf = '', timer = null, objectUrl = '', revision = 0;
''',
'''  let photo = null, csrf = '', timer = null, objectUrl = '', revision = 0, previewController = null;
''')

replace_once('app/static/editor.js',
'''  async function renderPreview() {
    if(!photo||!$('editorDialog').open)return;
    const current=++revision;
    $('editorStatus').textContent='Rendering RAW preview…';
    $('editorPreviewMessage').hidden=false;$('editorPreviewMessage').textContent='Rendering preview…';
    try {
      const response=await fetch(`/api/photos/${photo.id}/editor/preview`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(settings())});
      if(!response.ok)throw new Error(await responseError(response));
      const blob=await response.blob();
      if(current!==revision)return;
      releasePreview();objectUrl=URL.createObjectURL(blob);
      $('editorImage').src=objectUrl;$('editorImage').hidden=false;$('editorPreviewMessage').hidden=true;
      $('editorStatus').textContent='Preview rendered. Changes are non-destructive until Save JPEG.';
    } catch(err) {
      if(current!==revision)return;
      $('editorImage').hidden=true;$('editorPreviewMessage').hidden=false;$('editorPreviewMessage').textContent=err.message;
      $('editorStatus').textContent='RAW preview failed.';
    }
  }
  function schedulePreview(delay=450) {
    displayValues();clearTimeout(timer);timer=setTimeout(renderPreview,delay);
    $('downloadEdit').hidden=true;
  }
''',
'''  async function renderPreview() {
    if(!photo||!$('editorDialog').open)return;
    const current=++revision;
    if(previewController)previewController.abort();
    previewController=new AbortController();
    const hasPreview=!$('editorImage').hidden&&!!objectUrl;
    $('editorStatus').textContent=hasPreview?'Updating cached working preview…':'Preparing RAW working preview…';
    $('editorPreviewMessage').hidden=hasPreview;
    if(!hasPreview)$('editorPreviewMessage').textContent='Preparing RAW preview…';
    try {
      const response=await fetch(`/api/photos/${photo.id}/editor/preview`,{
        method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
        body:JSON.stringify(settings()),signal:previewController.signal
      });
      if(!response.ok)throw new Error(await responseError(response));
      const blob=await response.blob();
      if(current!==revision)return;
      const nextUrl=URL.createObjectURL(blob);
      await new Promise((resolve,reject)=>{
        const probe=new Image();probe.onload=resolve;probe.onerror=()=>reject(new Error('Rendered preview could not be decoded'));probe.src=nextUrl;
      });
      if(current!==revision){URL.revokeObjectURL(nextUrl);return;}
      const previous=objectUrl;objectUrl=nextUrl;
      $('editorImage').src=nextUrl;$('editorImage').hidden=false;$('editorPreviewMessage').hidden=true;
      if(previous)URL.revokeObjectURL(previous);
      $('editorStatus').textContent='Working preview updated. RAW decode is reused until denoise requires a new base.';
    } catch(err) {
      if(err.name==='AbortError'||current!==revision)return;
      if(!hasPreview){$('editorImage').hidden=true;$('editorPreviewMessage').hidden=false;$('editorPreviewMessage').textContent=err.message;}
      $('editorStatus').textContent=`RAW preview failed: ${err.message}`;
    }
  }
  function schedulePreview(delay=180) {
    displayValues();clearTimeout(timer);timer=setTimeout(renderPreview,delay);
    $('downloadEdit').hidden=true;
  }
''')

replace_once('app/static/editor.js',
'''    photo=selectedPhoto;csrf=token;revision++;clearTimeout(timer);releasePreview();
''',
'''    photo=selectedPhoto;csrf=token;revision++;clearTimeout(timer);if(previewController)previewController.abort();releasePreview();
''')
replace_once('app/static/editor.js',
'''  $('editorDialog').addEventListener('close',()=>{revision++;clearTimeout(timer);releasePreview();photo=null;});
''',
'''  $('editorDialog').addEventListener('close',()=>{revision++;clearTimeout(timer);if(previewController)previewController.abort();previewController=null;releasePreview();photo=null;});
''')

# --- Persistent editable camera/lens catalog reference overrides ---
Path('app/reference.py').write_text('''import hashlib\nimport json\n\nfrom app.db import Setting\n\nREFERENCE_OVERRIDES_KEY = 'catalog_reference_overrides_v1'\n\n\ndef load_reference_overrides(db):\n    setting = db.get(Setting, REFERENCE_OVERRIDES_KEY)\n    if not setting or not setting.value:\n        return {'camera': {}, 'lens': {}}\n    try:\n        value = json.loads(setting.value)\n    except (TypeError, ValueError):\n        return {'camera': {}, 'lens': {}}\n    if not isinstance(value, dict):\n        return {'camera': {}, 'lens': {}}\n    result = {'camera': {}, 'lens': {}}\n    for kind in result:\n        bucket = value.get(kind, {})\n        if isinstance(bucket, dict):\n            result[kind] = {str(name): fields for name, fields in bucket.items() if isinstance(fields, dict)}\n    return result\n\n\ndef reference_fingerprint(db):\n    setting = db.get(Setting, REFERENCE_OVERRIDES_KEY)\n    raw = setting.value if setting else ''\n    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]\n\n\ndef _field(value, label):\n    if value is None:\n        return ''\n    if not isinstance(value, str):\n        raise ValueError(f'{label} must be text')\n    value = value.strip()\n    if len(value) > 190 or any(ord(char) < 32 for char in value):\n        raise ValueError(f'Invalid {label.lower()}')\n    return value\n\n\ndef save_reference_override(db, kind, name, maker, mount):\n    if kind not in ('camera', 'lens'):\n        raise ValueError('Reference type must be camera or lens')\n    name = _field(name, 'Name')\n    if not name:\n        raise ValueError('Reference name is required')\n    maker = _field(maker, 'Maker')\n    mount = _field(mount, 'Lens mount')\n    overrides = load_reference_overrides(db)\n    fields = {}\n    if maker:\n        fields['maker'] = maker\n    if mount:\n        fields['mount'] = mount\n    if fields:\n        overrides[kind][name] = fields\n    else:\n        overrides[kind].pop(name, None)\n    encoded = json.dumps(overrides, ensure_ascii=False, sort_keys=True, separators=(',', ':'))\n    setting = db.get(Setting, REFERENCE_OVERRIDES_KEY)\n    if setting is None:\n        db.add(Setting(key=REFERENCE_OVERRIDES_KEY, value=encoded))\n    else:\n        setting.value = encoded\n    return fields\n''')

replace_once('app/statistics.py',
'''from app.db import Photo, Setting
''',
'''from app.db import Photo, Setting
from app.reference import load_reference_overrides, reference_fingerprint
''')
replace_once('app/statistics.py',
'''def _signature(db):
    count, latest = db.execute(select(func.count(Photo.id), func.max(Photo.indexed_at))).one()
    return f'{count}:{latest.isoformat() if latest else ""}'
''',
'''def _signature(db):
    count, latest = db.execute(select(func.count(Photo.id), func.max(Photo.indexed_at))).one()
    return f'{count}:{latest.isoformat() if latest else ""}:{reference_fingerprint(db)}'
''')
replace_once('app/statistics.py',
'''    camera_dated = Counter(); lens_dated = Counter()
    dated = total = 0

    statement = select(Photo.camera, Photo.lens, Photo.taken_at, Photo.metadata_json).execution_options(yield_per=2000)
''',
'''    camera_dated = Counter(); lens_dated = Counter()
    dated = total = 0
    overrides = load_reference_overrides(db)
    camera_overrides = overrides.get('camera', {})
    lens_overrides = overrides.get('lens', {})

    statement = select(Photo.camera, Photo.lens, Photo.taken_at, Photo.metadata_json).execution_options(yield_per=2000)
''')
replace_once('app/statistics.py',
'''        maker = _clean_text(metadata.get('Make'))
        if maker:
            makers[maker] += 1; camera_makers[camera][maker] += 1
        lens_maker = _clean_text(metadata.get('LensMake'))
        if lens_maker:
            lens_makers[lens_maker] += 1; lens_maker_counts[lens][lens_maker] += 1
''',
'''        camera_override = camera_overrides.get(camera, {})
        lens_override = lens_overrides.get(lens, {})
        maker = _clean_text(camera_override.get('maker')) or _clean_text(metadata.get('Make'))
        if maker:
            makers[maker] += 1; camera_makers[camera][maker] += 1
        lens_maker = _clean_text(lens_override.get('maker')) or _clean_text(metadata.get('LensMake'))
        if lens_maker:
            lens_makers[lens_maker] += 1; lens_maker_counts[lens][lens_maker] += 1
''')
replace_once('app/statistics.py',
'''        mount = _lens_mount(metadata, camera, lens)
        if mount:
''',
'''        mount = (_clean_text(lens_override.get('mount')) or _clean_text(camera_override.get('mount'))
                 or _lens_mount(metadata, camera, lens))
        if mount:
''')
replace_once('app/statistics.py',
'''    camera_breakdowns = {}
    for camera in cameras:
        camera_breakdowns[camera] = {
''',
'''    camera_breakdowns = {}
    for camera in cameras:
        override = camera_overrides.get(camera, {})
        camera_breakdowns[camera] = {
''')
replace_once('app/statistics.py',
'''            'maker': _first(camera_makers[camera]),
            'megapixels': _first(camera_megapixels[camera]),
''',
'''            'maker': _first(camera_makers[camera]),
            'override_maker': override.get('maker'),
            'override_mount': override.get('mount'),
            'megapixels': _first(camera_megapixels[camera]),
''')
replace_once('app/statistics.py',
'''    lens_breakdowns = {}
    for lens in lenses:
        lens_breakdowns[lens] = {
''',
'''    lens_breakdowns = {}
    for lens in lenses:
        override = lens_overrides.get(lens, {})
        lens_breakdowns[lens] = {
''')
replace_once('app/statistics.py',
'''            'maker': _first(lens_maker_counts[lens]),
            'mounts': _top(lens_mounts[lens], 5),
''',
'''            'maker': _first(lens_maker_counts[lens]),
            'override_maker': override.get('maker'),
            'override_mount': override.get('mount'),
            'mounts': _top(lens_mounts[lens], 5),
''')

replace_once('app/web.py',
'''from app.statistics import build_statistics
''',
'''from app.statistics import build_statistics
from app.reference import save_reference_override
''')
replace_once('app/web.py',
'''    @app.get('/api/statistics')
    def statistics():
        force = request.args.get('refresh') == '1'
        with Session.begin() as db:
            return jsonify(build_statistics(db, force=force))

    @app.get('/api/scan')
''',
'''    @app.get('/api/statistics')
    def statistics():
        force = request.args.get('refresh') == '1'
        with Session.begin() as db:
            return jsonify(build_statistics(db, force=force))

    @app.put('/api/statistics/reference')
    def update_statistics_reference():
        data = request.get_json(silent=True) or {}
        kind = data.get('type')
        name = data.get('name')
        if kind not in ('camera', 'lens') or not isinstance(name, str) or not name.strip():
            abort(400, 'Reference type and name are required')
        name = name.strip()[:190]
        column = Photo.camera if kind == 'camera' else Photo.lens
        with Session.begin() as db:
            if not db.scalar(select(func.count()).select_from(Photo).where(column == name)):
                abort(404, f'{kind.capitalize()} not found in catalog')
            try:
                saved = save_reference_override(db, kind, name, data.get('maker'), data.get('mount'))
            except ValueError as exc:
                abort(400, str(exc))
        return jsonify(ok=True, type=kind, name=name, override=saved)

    @app.get('/api/scan')
''')

# Interactive hover/reference card form.
replace_once('app/static/statistics.html',
'''<aside id="referenceCard" class="reference-card" role="tooltip" hidden><div class="reference-type" id="referenceType"></div><h3 id="referenceTitle"></h3><dl id="referenceFacts"></dl><div id="referenceFooter" class="reference-footer"></div></aside>
''',
'''<aside id="referenceCard" class="reference-card" role="dialog" aria-labelledby="referenceTitle" hidden><div class="reference-type" id="referenceType"></div><h3 id="referenceTitle"></h3><dl id="referenceFacts"></dl><div id="referenceFooter" class="reference-footer"></div><div id="referenceActions" class="reference-actions"><button id="editReference" type="button">Edit catalog facts</button></div><form id="referenceEditForm" class="reference-edit" hidden><label for="referenceMaker">Maker</label><input id="referenceMaker" type="text" maxlength="190" autocomplete="off"><label for="referenceMount">Lens mount</label><input id="referenceMount" type="text" maxlength="190" autocomplete="off"><p class="reference-help">Blank fields use indexed EXIF/inferred values. Saved overrides apply to statistics without changing the RAW metadata.</p><div class="reference-edit-actions"><button class="primary" type="submit">Save</button><button id="referenceClear" type="button">Use indexed EXIF</button><button id="referenceCancel" type="button">Cancel</button></div><p id="referenceEditStatus" class="reference-edit-status"></p></form></aside>
''')

js = Path('app/static/statistics.js')
text = js.read_text()
text = text.replace("let csrf = '', archiveData = null, selectedCamera = '';", "let csrf = '', archiveData = null, selectedCamera = '', referenceState = null, referenceHideTimer = null, referenceEditing = false;", 1)
start = text.index('function addReferenceHandlers(')
end = text.index('\nfunction renderBars(', start)
new_block = r'''function cancelReferenceHide(){clearTimeout(referenceHideTimer);referenceHideTimer=null;}
function scheduleReferenceHide(){cancelReferenceHide();referenceHideTimer=setTimeout(()=>hideReference(),220);}
function addReferenceHandlers(target,type,name){
  target.classList.add('has-reference');
  const mark=document.createElement('span');mark.className='reference-mark';mark.textContent='ⓘ';mark.setAttribute('aria-hidden','true');target.append(mark);
  target.addEventListener('mouseenter',()=>showReference(target,type,name));
  target.addEventListener('mouseleave',scheduleReferenceHide);
  target.addEventListener('focus',()=>showReference(target,type,name));
  target.addEventListener('blur',scheduleReferenceHide);
}
function fact(label,value){
  if(value===null||value===undefined||value==='')return null;
  const dt=document.createElement('dt');dt.textContent=label;
  const dd=document.createElement('dd');dd.textContent=String(value);
  return [dt,dd];
}
function firstValue(rows){return rows?.length?rows[0].value:null;}
function topValues(rows,limit=2){return (rows||[]).slice(0,limit).map(x=>x.value).join(', ')||null;}
function populateReference(type,name,detail){
  $('referenceType').textContent=type==='camera'?'CAMERA · CATALOG REFERENCE':'LENS · CATALOG REFERENCE';$('referenceTitle').textContent=name;
  const facts=$('referenceFacts');facts.replaceChildren();
  const share=`${nf.format(detail.total_photos)} · ${pct(detail.total_photos,archiveData.total_photos)}% of archive`;
  const rows=type==='camera'?[
    fact('Maker',detail.maker||'Not reported'),fact('Usage',share),fact('Resolution',detail.megapixels||'Not reported'),
    fact('Sensor',detail.sensor_size||'Not reported'),fact('Lens mount',topValues(detail.mounts)||'Not reported'),
    fact('Lenses used',nf.format(detail.distinct_lenses||0)),fact('Most used lens',firstValue(detail.lenses)),fact('Active years',detail.active_years||'No capture dates')
  ]:[
    fact('Maker',detail.maker||'Not reported'),fact('Usage',share),fact('Lens mount',topValues(detail.mounts)||'Not reported'),
    fact('Cameras used',nf.format(detail.distinct_cameras||0)),fact('Most used camera',firstValue(detail.cameras)),
    fact('Common focal length',firstValue(detail.focal_lengths)),fact('Common aperture',firstValue(detail.apertures)),fact('Active years',detail.active_years||'No capture dates')
  ];
  for(const row of rows)if(row)facts.append(...row);
  const overridden=detail.override_maker||detail.override_mount;
  $('referenceFooter').textContent=overridden?'Catalog override active. It replaces indexed EXIF only in catalog statistics.':'Derived from this catalog’s indexed EXIF metadata; it is not an external product database.';
}
function positionReference(target){
  const card=$('referenceCard');const rect=target.getBoundingClientRect();
  const width=Math.min(380,window.innerWidth-24);card.style.width=`${width}px`;
  const cardRect=card.getBoundingClientRect();let left=Math.min(rect.left,window.innerWidth-cardRect.width-12);left=Math.max(12,left);
  let top=rect.bottom+8;if(top+cardRect.height>window.innerHeight-12)top=Math.max(12,rect.top-cardRect.height-8);
  card.style.left=`${left}px`;card.style.top=`${top}px`;
}
function showReference(target,type,name){
  if(!archiveData||referenceEditing)return;
  const detail=type==='camera'?archiveData.camera_breakdowns?.[name]:archiveData.lens_breakdowns?.[name];if(!detail)return;
  cancelReferenceHide();referenceState={target,type,name,detail};populateReference(type,name,detail);
  $('referenceFacts').hidden=false;$('referenceFooter').hidden=false;$('referenceActions').hidden=false;$('referenceEditForm').hidden=true;
  const card=$('referenceCard');card.hidden=false;positionReference(target);
}
function hideReference(force=false){
  cancelReferenceHide();if(referenceEditing&&!force)return;
  referenceEditing=false;referenceState=null;$('referenceCard').hidden=true;$('referenceEditForm').hidden=true;
}
function beginReferenceEdit(){
  if(!referenceState)return;cancelReferenceHide();referenceEditing=true;
  const detail=referenceState.detail;$('referenceFacts').hidden=true;$('referenceFooter').hidden=true;$('referenceActions').hidden=true;$('referenceEditForm').hidden=false;
  $('referenceMaker').value=detail.override_maker||'';$('referenceMaker').placeholder=detail.maker||'Not reported';
  $('referenceMount').value=detail.override_mount||'';$('referenceMount').placeholder=topValues(detail.mounts)||'Not reported';
  $('referenceEditStatus').textContent='';positionReference(referenceState.target);$('referenceMaker').focus();
}
async function saveReference(clear=false){
  if(!referenceState)return;const {type,name}=referenceState;
  $('referenceEditStatus').textContent='Saving…';
  try{
    await api('/api/statistics/reference',{method:'PUT',body:JSON.stringify({type,name,maker:clear?'':$('referenceMaker').value.trim(),mount:clear?'':$('referenceMount').value.trim()})});
    referenceEditing=false;hideReference(true);await load(true);
  }catch(err){$('referenceEditStatus').textContent=err.message;showError(err);}
}
'''
text = text[:start] + new_block + text[end:]
old_bottom = "$('clearCameraScope').onclick=clearCamera;\n$('refreshStats').onclick=()=>load(true);\n$('signOut').onclick=async()=>{try{await api('/api/logout',{method:'POST'});}finally{location.href='/';}};\nwindow.addEventListener('scroll',hideReference,{passive:true});window.addEventListener('resize',hideReference);"
new_bottom = "$('clearCameraScope').onclick=clearCamera;\n$('refreshStats').onclick=()=>load(true);\n$('signOut').onclick=async()=>{try{await api('/api/logout',{method:'POST'});}finally{location.href='/';}};\n$('referenceCard').addEventListener('mouseenter',cancelReferenceHide);$('referenceCard').addEventListener('mouseleave',scheduleReferenceHide);\n$('editReference').onclick=beginReferenceEdit;$('referenceCancel').onclick=()=>{referenceEditing=false;if(referenceState)showReference(referenceState.target,referenceState.type,referenceState.name);};\n$('referenceClear').onclick=()=>saveReference(true);$('referenceEditForm').onsubmit=e=>{e.preventDefault();saveReference(false);};\nwindow.addEventListener('scroll',()=>hideReference(true),{passive:true});window.addEventListener('resize',()=>hideReference(true));"
if old_bottom not in text:
    raise RuntimeError('statistics.js bottom handlers not found')
text = text.replace(old_bottom, new_bottom, 1)
js.write_text(text)

replace_once('app/static/statistics.css',
'''.reference-card{position:fixed;z-index:2000;background:#f3f0e8;color:#191919;border:1px solid #c8c2b5;border-radius:8px;padding:1rem;box-shadow:0 14px 38px rgba(0,0,0,.45);pointer-events:none;max-width:calc(100vw - 24px)}
''',
'''.reference-card{position:fixed;z-index:2000;background:#f3f0e8;color:#191919;border:1px solid #c8c2b5;border-radius:8px;padding:1rem;box-shadow:0 14px 38px rgba(0,0,0,.45);pointer-events:auto;max-width:calc(100vw - 24px)}
''')
replace_once('app/static/statistics.css',
'''.reference-footer{border-top:1px solid #d7d2c7;margin-top:.75rem;padding-top:.55rem;font-size:.68rem;line-height:1.35;color:#706a61}.wide-card''',
'''.reference-footer{border-top:1px solid #d7d2c7;margin-top:.75rem;padding-top:.55rem;font-size:.68rem;line-height:1.35;color:#706a61}.reference-actions{margin-top:.75rem}.reference-actions button,.reference-edit button{font-size:.72rem;padding:.42rem .58rem;background:#e7e2d7;color:#292722;border-color:#b8b1a4}.reference-actions button:hover,.reference-edit button:hover{background:#dcd5c7}.reference-edit{border-top:1px solid #d7d2c7;margin-top:.7rem;padding-top:.7rem}.reference-edit label{font-size:.7rem;color:#555047;margin:.55rem 0 .3rem}.reference-edit input{background:#fff;color:#191919;border-color:#b8b1a4;padding:.5rem;font-size:.76rem}.reference-edit-actions{display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.75rem}.reference-edit-actions button.primary{background:#596b35;border-color:#596b35;color:#fff}.reference-help,.reference-edit-status{font-size:.67rem;line-height:1.35;color:#706a61;margin:.55rem 0 0}.reference-edit-status{color:#5b3f28}.wide-card''')

# Tests for cache reuse and persistent reference overrides.
replace_once('tests/test_editor.py',
'''import pytest
from PIL import Image

from app.editor import DEFAULT_SETTINGS, normalize_settings, _tone_image
''',
'''import pytest
from PIL import Image

import app.editor as editor
from app.editor import DEFAULT_SETTINGS, normalize_settings, _tone_image
''')
with Path('tests/test_editor.py').open('a') as f:
    f.write('''\n\ndef test_editor_working_preview_cache_reuses_raw_decode(tmp_path, monkeypatch):\n    source = tmp_path / 'test.raw'\n    source.write_bytes(b'raw')\n    monkeypatch.setenv('CACHE_DIR', str(tmp_path / 'cache'))\n    calls = []\n    def fake_decode(path, denoise=0, half_size=False):\n        calls.append((denoise, half_size))\n        return Image.new('RGB', (120, 80), (100, 110, 120))\n    monkeypatch.setattr(editor, '_decode_raw', fake_decode)\n    first = editor.render(source, normalize_settings({'exposure': 0}), max_edge=1600)\n    first.close()\n    second = editor.render(source, normalize_settings({'exposure': 1}), max_edge=1600)\n    second.close()\n    assert len(calls) == 1\n    third = editor.render(source, normalize_settings({'denoise': 70}), max_edge=1600)\n    third.close()\n    assert len(calls) == 2\n''')

with Path('tests/test_catalog.py').open('a') as f:
    f.write('''\n\ndef test_reference_overrides_are_saved_and_used(client):\n    seed_dates()\n    h = headers(client)\n    camera = client.put('/api/statistics/reference', json={'type':'camera','name':'Canon EOS R6','maker':'Canon corrected','mount':'Canon RF'}, headers=h)\n    assert camera.status_code == 200\n    lens = client.put('/api/statistics/reference', json={'type':'lens','name':'EF 50mm f/1.2L','maker':'Canon Lens','mount':'Canon EF'}, headers=h)\n    assert lens.status_code == 200\n    stats = client.get('/api/statistics').json\n    detail = stats['camera_breakdowns']['Canon EOS R6']\n    assert detail['maker'] == 'Canon corrected' and detail['override_mount'] == 'Canon RF'\n    lens_detail = stats['lens_breakdowns']['EF 50mm f/1.2L']\n    assert lens_detail['maker'] == 'Canon Lens' and lens_detail['mounts'][0]['value'] == 'Canon EF'\n    with Session() as db:\n        value = db.get(Setting, 'catalog_reference_overrides_v1').value\n        assert 'Canon corrected' in value and 'Canon Lens' in value\n    cleared = client.put('/api/statistics/reference', json={'type':'lens','name':'EF 50mm f/1.2L','maker':'','mount':''}, headers=h)\n    assert cleared.status_code == 200 and cleared.json['override'] == {}\n''')

print('patch applied')
