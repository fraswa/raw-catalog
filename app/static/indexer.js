
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
