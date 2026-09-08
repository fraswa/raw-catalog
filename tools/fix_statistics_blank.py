from pathlib import Path


def replace(path, old, new):
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'anchor missing in {path}: {old!r}')
    p.write_text(text.replace(old, new, 1), encoding='utf-8')

replace(
    'app/web.py',
    "        if request.path.startswith('/api/'):\n            response.headers['Cache-Control'] = 'no-store'\n        if request.path.startswith('/media/'):\n            response.headers['Cache-Control'] = 'private, max-age=3600'\n",
    "        if request.path.startswith('/api/'):\n            response.headers['Cache-Control'] = 'no-store'\n        elif request.path.startswith('/static/') or request.path in ('/', '/settings', '/edits', '/statistics', '/catalog', '/indexer'):\n            # HTML, JS and CSS are deployed together. Force revalidation so a browser cannot\n            # combine a cached page with a newer script (or vice versa) after an upgrade.\n            response.headers['Cache-Control'] = 'no-cache, max-age=0, must-revalidate'\n        if request.path.startswith('/media/'):\n            response.headers['Cache-Control'] = 'private, max-age=3600'\n",
)

replace(
    'app/static/statistics.js',
    "function populateMounts(){const s=$('mountFilter'),value=selectedMount;s.replaceChildren(new Option('All lens mounts',''));for(const r of archiveData.mounts||[])s.append(new Option(`${r.value} (${nf.format(r.count)})`,r.value));s.value=value}\n",
    "function populateMounts(){const s=$('mountFilter');if(!s)return;const value=selectedMount;s.replaceChildren(new Option('All lens mounts',''));for(const r of archiveData.mounts||[])s.append(new Option(`${r.value} (${nf.format(r.count)})`,r.value));s.value=value}\n",
)

old = "$('clearCameraScope').onclick=clearCamera;$('clearMountScope').onclick=clearMount;$('mountFilter').onchange=e=>selectMount(e.target.value);$('showAllCameras').onchange=renderCameraUsage;$('showAllLenses').onchange=renderLensUsage;$('refreshStats').onclick=()=>load(true);$('signOut').onclick=async()=>{try{await api('/api/logout',{method:'POST'})}finally{location.href='/'}};$('referenceCard').onmouseenter=cancelReferenceHide;$('referenceCard').onmouseleave=scheduleReferenceHide;$('editReference').onclick=beginReferenceEdit;$('referenceCancel').onclick=()=>{referenceEditing=false;if(referenceState)showReference(referenceState.target,referenceState.type,referenceState.name)};$('referenceClear').onclick=()=>saveReference(true);$('referenceEditForm').onsubmit=e=>{e.preventDefault();saveReference(false)};addEventListener('scroll',()=>hideReference(true),{passive:true});addEventListener('resize',()=>hideReference(true));\n"
new = "function bind(id,prop,handler){const node=$(id);if(node)node[prop]=handler}\nbind('clearCameraScope','onclick',clearCamera);bind('clearMountScope','onclick',clearMount);bind('mountFilter','onchange',e=>selectMount(e.target.value));bind('showAllCameras','onchange',renderCameraUsage);bind('showAllLenses','onchange',renderLensUsage);bind('refreshStats','onclick',()=>load(true));bind('signOut','onclick',async()=>{try{await api('/api/logout',{method:'POST'})}finally{location.href='/'}});bind('referenceCard','onmouseenter',cancelReferenceHide);bind('referenceCard','onmouseleave',scheduleReferenceHide);bind('editReference','onclick',beginReferenceEdit);bind('referenceCancel','onclick',()=>{referenceEditing=false;if(referenceState)showReference(referenceState.target,referenceState.type,referenceState.name)});bind('referenceClear','onclick',()=>saveReference(true));bind('referenceEditForm','onsubmit',e=>{e.preventDefault();saveReference(false)});addEventListener('scroll',()=>hideReference(true),{passive:true});addEventListener('resize',()=>hideReference(true));\n"
replace('app/static/statistics.js', old, new)

# Cache-bust the Statistics JS on the matching HTML as another layer of protection.
replace('app/static/statistics.html', '<script defer src="/static/statistics.js"></script>', '<script defer src="/static/statistics.js?v=6"></script>')
