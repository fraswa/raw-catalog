'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let csrf='';
  const nf=new Intl.NumberFormat();
  function formatBytes(value){if(!value)return'0 B';const units=['B','KB','MB','GB','TB'];const index=Math.min(units.length-1,Math.floor(Math.log(value)/Math.log(1024)));return`${(value/(1024**index)).toFixed(index?1:0)} ${units[index]}`;}
  async function api(url,options={}){const response=await fetch(url,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...(options.headers||{})}});const result=await response.json();if(!response.ok)throw new Error(result.error||`Request failed (${response.status})`);return result;}
  function show(data){$('editFolder').value=data.folder;$('editPath').textContent=data.path;$('editFiles').textContent=nf.format(data.files);$('editBytes').textContent=formatBytes(data.bytes);$('saveEditFolder').disabled=!!data.active;$('editFolder').disabled=!!data.active;$('editStorageHint').textContent=data.active?'Storage location cannot be changed while an index/rebuild job is active.':'Edited JPEGs are permanent outputs; changing this path does not move existing files.';}
  function fail(err){const box=$('settingsError');box.textContent=err.message;box.hidden=false;}
  async function load(){const state=await api('/api/session');csrf=state.csrf;if(!state.authenticated)return;show(await api('/api/settings/edits'));}
  $('editFolderForm').onsubmit=async event=>{event.preventDefault();$('settingsError').hidden=true;$('saveEditFolder').disabled=true;$('editActionMessage').textContent='Saving edited JPEG location…';try{const data=await api('/api/settings/edits',{method:'PUT',body:JSON.stringify({folder:$('editFolder').value.trim()})});show(data);$('editActionMessage').textContent='Edited JPEG destination saved.';}catch(err){$('editActionMessage').textContent='';fail(err);}finally{if(!$('editFolder').disabled)$('saveEditFolder').disabled=false;}};
  load().catch(fail);
})();
