'use strict';
const $ = id => document.getElementById(id);
let csrf = '', items = [], cursor = null, total = 0, viewing = -1, revision = 0, detailRevision = 0;
let loading = false, authenticated = false, scanSignature = '', timer, browsePath = '';
const nf = new Intl.NumberFormat();
async function api(url, options = {}) {
  const response = await fetch(url, {...options, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...(options.headers || {})}});
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && !url.endsWith('/login')) showLogin();
    throw new Error(result.error || `Request failed (${response.status})`);
  }
  return result;
}
function error(err) { $('error').textContent = err.message; $('error').hidden = false; }
function showLogin() { authenticated = false; clearTimeout(timer); $('application').hidden = true; $('login').hidden = false; if ($('viewer').open) $('viewer').close(); }
function params() { const p = new URLSearchParams(); for (const [name, id] of [['camera','camera'],['lens','lens'],['q','query']]) if ($(id).value) p.set(name,$(id).value); return p; }
function optionList(id, values) {
  const select = $(id), selected = select.value;
  select.replaceChildren(new Option(id === 'camera' ? 'All cameras' : 'All lenses', ''));
  for (const item of values) select.add(new Option(`${item.value} (${nf.format(item.count)})`, item.value));
  if (selected && !values.some(v => v.value === selected)) select.add(new Option(`${selected} (0)`, selected));
  select.value = selected;
}
function card(photo, index) {
  const button = document.createElement('button'); button.className = 'photo';
  button.setAttribute('aria-label', `Preview ${photo.filename}, ${photo.camera}, ${photo.lens}`);
  const wrap = document.createElement('div'); wrap.className = 'image-wrap';
  const missing = () => { const msg = document.createElement('span'); msg.className = 'missing'; msg.textContent = 'Preview unavailable'; wrap.replaceChildren(msg); };
  if (photo.thumbnail) { const img = document.createElement('img'); img.src = photo.thumbnail; img.loading = 'lazy'; img.decoding = 'async'; img.alt = photo.filename; img.onerror = missing; wrap.append(img); } else missing();
  const info = document.createElement('div'); info.className = 'photo-info';
  for (const [tag, text, cls] of [['strong', photo.filename, ''], ['span', photo.camera, ''], ['span', photo.lens, ''], ['span', photo.taken_at ? photo.taken_at.slice(0,10) : 'Capture date unavailable', 'date']]) { const e = document.createElement(tag); e.textContent = text; e.title = text; e.className = cls; info.append(e); }
  button.append(wrap,info); button.onclick = () => view(index); return button;
}
async function search(append = false) {
  if (append && (loading || !cursor)) return false;
  const rev = append ? revision : ++revision;
  loading = true; $('loadMore').disabled = true; $('error').hidden = true;
  const p = params(); if (append) p.set('after', cursor);
  if (!append) $('summary').textContent = 'Searching photographs…';
  try {
    const [data, facets] = await Promise.all([api('/api/photos?' + p), append ? null : api('/api/facets?' + p)]);
    if (rev !== revision) return false;
    if (!append) { items = []; $('grid').replaceChildren(); }
    const start = items.length; items.push(...data.items); cursor = data.next_cursor; total = data.total;
    data.items.forEach((photo, i) => $('grid').append(card(photo, start+i)));
    if (facets) { optionList('camera',facets.camera); optionList('lens',facets.lens); }
    $('count').textContent = nf.format(total);
    $('summary').textContent = `${nf.format(items.length)} of ${nf.format(total)} photographs`;
    $('loadMore').hidden = !cursor; $('empty').hidden = items.length > 0;
    const filtered = params().toString().length > 0;
    $('emptyTitle').textContent = filtered ? 'No matching photographs' : 'Your archive starts here';
    $('emptyText').textContent = filtered ? 'Try a different camera or lens, or clear your filters.' : 'Click Index photos to scan your configured photo folder.';
    return true;
  } catch(err) { if (rev === revision) { error(err); $('summary').textContent = 'Could not load photographs'; } return false; }
  finally { if (rev === revision) { loading = false; $('loadMore').disabled = false; } }
}
async function view(index) {
  if (index < 0) return;
  if (index >= items.length && cursor) await search(true);
  if (index >= items.length) return;
  viewing = index; const photo = items[index], rev = ++detailRevision;
  $('viewerTitle').textContent = photo.filename; $('viewerPosition').textContent = `${index+1} of ${nf.format(total)} matching photographs`;
  $('viewerCamera').textContent = photo.camera; $('viewerLens').textContent = photo.lens;
  $('viewerPath').textContent = ''; $('metadata').textContent = ''; $('exposure').textContent = '';
  $('previous').disabled = index === 0; $('next').disabled = index === items.length-1 && !cursor;
  $('previewImage').hidden = true; $('previewImage').removeAttribute('src');
  $('previewMessage').hidden = false; $('previewMessage').textContent = photo.preview ? 'Loading preview…' : 'Preview unavailable. Check file details for the indexing error.';
  $('previewImage').onload = () => { if(rev !== detailRevision) return; $('previewImage').hidden = false; $('previewMessage').hidden = true; };
  $('previewImage').onerror = () => { if(rev !== detailRevision) return; $('previewImage').hidden = true; $('previewMessage').hidden = false; $('previewMessage').textContent = 'Preview could not be loaded. Run a scan to regenerate missing previews.'; };
  if (photo.preview) { $('previewImage').alt = photo.filename; $('previewImage').src = photo.preview; }
  if (!$('viewer').open) $('viewer').showModal();
  try {
    const detail = await api(`/api/photos/${photo.id}`); if (rev !== detailRevision) return;
    $('viewerPath').textContent = detail.path; $('metadata').textContent = JSON.stringify(detail.metadata, null, 2);
    if (detail.preview_error) $('metadata').textContent = `Preview error: ${detail.preview_error}\n\n` + $('metadata').textContent;
    const m = detail.metadata;
    $('exposure').textContent = [m.FocalLength, m.FNumber ? `f/${m.FNumber}` : null, m.ExposureTime ? `${m.ExposureTime} s` : null, m.ISO ? `ISO ${m.ISO}` : null].filter(Boolean).join(' · ');
  } catch(err) { if (rev === detailRevision) $('metadata').textContent = err.message; }
}
async function loadFolders(path = '') {
  const data = await api('/api/folders?path=' + encodeURIComponent(path));
  browsePath = data.current;
  $('folderCurrent').textContent = data.current_display;
  $('folderUp').disabled = data.parent === null;
  $('folderUp').dataset.path = data.parent ?? '';
  $('folderList').replaceChildren();
  if (!data.directories.length) {
    const empty = document.createElement('span'); empty.className = 'folder-empty'; empty.textContent = 'No subfolders'; $('folderList').append(empty);
  }
  for (const folder of data.directories) {
    const button = document.createElement('button'); button.className = 'folder-entry'; button.textContent = folder.name;
    button.onclick = () => loadFolders(folder.path).catch(error);
    $('folderList').append(button);
  }
  return data;
}
async function pollScan() {
  if (!authenticated) return;
  try {
    const data = await api('/api/scan'), job = data.scan, active = job && ['queued','running'].includes(job.state);
    const rebuilding = active && (job.message || '').toLowerCase().includes('thumbnail');
    $('scanButton').disabled = !!active; $('scanButton').textContent = rebuilding ? 'Rebuilding thumbnails…' : (active ? 'Indexing…' : 'Index photos');
    $('force').disabled = !!active; $('cancelScan').hidden = !active;
    $('browseFolder').disabled = !!active;
    $('cancelScan').disabled = !!job?.cancel; $('cancelScan').textContent = job?.cancel ? 'Cancelling…' : 'Cancel scan';
    $('scanState').textContent = job ? job.state : 'Ready';
    $('scanMessage').textContent = job ? job.message : `Photo folder: ${data.root}`;
    $('scanCounts').textContent = job ? `${nf.format(job.discovered)} found · ${nf.format(job.indexed)} processed · ${nf.format(job.skipped)} unchanged · ${nf.format(job.errors)} errors` : '';
    $('scanPath').textContent = job?.current_path || '';
    $('selectedFolder').textContent = data.root; $('selectedFolder').dataset.path = data.selected || '';
    if (job && active && Date.now() - Date.parse(job.updated_at) > 300000) $('scanMessage').textContent += ' — No recent progress. Check worker logs.';
    const signature = job ? `${job.id}:${job.state}` : '';
    if (scanSignature && signature !== scanSignature && !active && !$('viewer').open) await search();
    scanSignature = signature;
  } catch(err) { error(err); }
  finally { if(authenticated) timer = setTimeout(pollScan, 3000); }
}
async function enter() {
  authenticated = true; $('login').hidden = true; $('application').hidden = false;
  await search(); clearTimeout(timer); pollScan();
}
$('loginForm').onsubmit = async e => { e.preventDefault(); $('loginError').textContent = ''; try { const state = await api('/api/session'); csrf = state.csrf; const result = await api('/api/login', {method:'POST',body:JSON.stringify({password:$('password').value})}); csrf = result.csrf; $('password').value = ''; await enter(); } catch(err) { $('loginError').textContent = err.message; } };
$('signOut').onclick = async () => { try { await api('/api/logout',{method:'POST'}); showLogin(); } catch(err) { error(err); } };
$('filters').onsubmit = e => { e.preventDefault(); search(); };
$('camera').onchange = $('lens').onchange = () => search();
let debounce; $('query').oninput = () => { clearTimeout(debounce); debounce = setTimeout(() => search(),300); };
$('reset').onclick = () => { clearTimeout(debounce); $('camera').value = ''; $('lens').value = ''; $('query').value = ''; search(); };
$('loadMore').onclick = () => search(true);
$('scanButton').onclick = async () => { $('scanButton').disabled = true; try { await api('/api/scan',{method:'POST',body:JSON.stringify({force:$('force').checked})}); $('scanPanel').open = true; clearTimeout(timer); await pollScan(); } catch(err) { error(err); $('scanButton').disabled = false; } };
$('cancelScan').onclick = async () => { try { await api('/api/scan/cancel',{method:'POST'}); clearTimeout(timer); await pollScan(); } catch(err) { error(err); } };
$('browseFolder').onclick = async () => {
  $('folderBrowser').hidden = false;
  try { await loadFolders($('selectedFolder').dataset.path || ''); }
  catch(err) { try { await loadFolders(''); } catch(rootErr) { error(rootErr); $('folderBrowser').hidden = true; } }
};
$('closeFolderBrowser').onclick = () => { $('folderBrowser').hidden = true; };
$('folderUp').onclick = () => loadFolders($('folderUp').dataset.path || '').catch(error);
$('selectFolder').onclick = async () => {
  $('selectFolder').disabled = true;
  try {
    const data = await api('/api/folders/select',{method:'POST',body:JSON.stringify({path:browsePath})});
    $('selectedFolder').textContent = data.selected_display; $('selectedFolder').dataset.path = data.selected;
    $('folderBrowser').hidden = true; clearTimeout(timer); await pollScan();
  } catch(err) { error(err); }
  finally { $('selectFolder').disabled = false; }
};
$('closeViewer').onclick = () => $('viewer').close();
$('viewer').addEventListener('close', () => { detailRevision++; if(document.fullscreenElement) document.exitFullscreen().catch(()=>{}); });
$('previous').onclick = () => view(viewing-1); $('next').onclick = () => view(viewing+1);
$('fullScreen').onclick = async () => { try { if (document.fullscreenElement) await document.exitFullscreen(); else if($('viewer').requestFullscreen) await $('viewer').requestFullscreen(); } catch(err) { $('metadata').textContent = err.message; } };
if (!$('viewer').requestFullscreen) $('fullScreen').hidden = true;
document.addEventListener('keydown', e => { if (!$('viewer').open) return; if(e.key === 'ArrowRight') { e.preventDefault(); view(viewing+1); } if(e.key === 'ArrowLeft') { e.preventDefault(); view(viewing-1); } });
(async () => { try { const state = await api('/api/session'); csrf = state.csrf; if(state.authenticated) await enter(); else showLogin(); } catch(err) { showLogin(); $('loginError').textContent = err.message; } })();
