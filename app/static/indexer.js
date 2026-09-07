'use strict';
const $ = id => document.getElementById(id);
const nf = new Intl.NumberFormat();
let csrf = '', timer, browsePath = '', authenticated = false;

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
function setBusy(busy){
  for(const id of ['browseFolder','skipPreviews','skipImported','force','startScan']) $(id).disabled=busy;
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
$('skipImported').onchange=$('force').onchange=syncOptions;
$('browseFolder').onclick=async()=>{$('folderBrowser').hidden=false;try{await loadFolders($('selectedFolder').dataset.path||'');}catch(err){try{await loadFolders('');}catch(rootErr){showError(rootErr);$('folderBrowser').hidden=true;}}};
$('closeFolderBrowser').onclick=()=>{$('folderBrowser').hidden=true;};
$('folderUp').onclick=()=>loadFolders($('folderUp').dataset.path||'').catch(showError);
$('selectFolder').onclick=async()=>{clearError();$('selectFolder').disabled=true;try{const data=await api('/api/folders/select',{method:'POST',body:JSON.stringify({path:browsePath})});$('selectedFolder').textContent=data.selected_display;$('selectedFolder').dataset.path=data.selected;$('folderBrowser').hidden=true;}catch(err){showError(err);}finally{$('selectFolder').disabled=false;}};
$('startScan').onclick=async()=>{clearError();$('startScan').disabled=true;try{await api('/api/scan',{method:'POST',body:JSON.stringify({force:$('force').checked,skip_previews:$('skipPreviews').checked,skip_imported:$('skipImported').checked})});clearTimeout(timer);await poll();}catch(err){showError(err);$('startScan').disabled=false;}};
$('cancelScan').onclick=async()=>{clearError();try{await api('/api/scan/cancel',{method:'POST'});clearTimeout(timer);await poll();}catch(err){showError(err);}};
$('signOut').onclick=async()=>{try{await api('/api/logout',{method:'POST'});}finally{location.href='/';}};
(async()=>{try{const state=await api('/api/session');csrf=state.csrf;if(!state.authenticated){location.href='/';return;}authenticated=true;$('indexerApp').hidden=false;syncOptions();poll();}catch(err){showError(err);$('indexerApp').hidden=false;}})();
