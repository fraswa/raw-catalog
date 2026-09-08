'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const defaults = {exposure:0,temperature:6500,tint:0,black:0,white:255,denoise:0};
  const controls = ['exposure','temperature','tint','black','white','denoise'];
  let photo = null, csrf = '', timer = null, objectUrl = '', revision = 0;

  function settings() {
    return {
      exposure:Number($('editExposure').value),
      temperature:Number($('editTemperature').value),
      tint:Number($('editTint').value),
      black:Number($('editBlack').value),
      white:Number($('editWhite').value),
      denoise:Number($('editDenoise').value)
    };
  }
  function displayValues() {
    const s=settings();
    $('editExposureValue').textContent=`${s.exposure>0?'+':''}${s.exposure.toFixed(1)} EV`;
    $('editTemperatureValue').textContent=`${Math.round(s.temperature)} K`;
    $('editTintValue').textContent=`${s.tint>0?'+':''}${Math.round(s.tint)}`;
    $('editBlackValue').textContent=String(Math.round(s.black));
    $('editWhiteValue').textContent=String(Math.round(s.white));
    $('editDenoiseValue').textContent=`${Math.round(s.denoise)}%`;
  }
  function applySettings(value) {
    const s={...defaults,...(value||{})};
    $('editExposure').value=s.exposure;$('editTemperature').value=s.temperature;$('editTint').value=s.tint;
    $('editBlack').value=s.black;$('editWhite').value=s.white;$('editDenoise').value=s.denoise;
    displayValues();
  }
  async function responseError(response) {
    try { const body=await response.json(); return body.error||`Request failed (${response.status})`; }
    catch { return `Request failed (${response.status})`; }
  }
  async function json(url, options={}) {
    const response=await fetch(url,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...(options.headers||{})}});
    if(!response.ok) throw new Error(await responseError(response));
    return response.json();
  }
  function releasePreview() {
    if(objectUrl){URL.revokeObjectURL(objectUrl);objectUrl='';}
  }
  async function renderPreview() {
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
  async function autoAdjust() {
    if(!photo)return;
    $('autoEdit').disabled=true;$('editorStatus').textContent='Analyzing RAW…';
    try {
      const data=await json(`/api/photos/${photo.id}/editor/auto`,{method:'POST',body:'{}'});
      applySettings(data.settings);await renderPreview();
    } catch(err) {$('editorStatus').textContent=err.message;}
    finally {$('autoEdit').disabled=false;}
  }
  async function save() {
    if(!photo)return;
    $('saveEdit').disabled=true;$('editorStatus').textContent='Rendering full-resolution JPEG…';
    try {
      const data=await json(`/api/photos/${photo.id}/editor/save`,{method:'POST',body:JSON.stringify(settings())});
      $('editorStatus').textContent=`Saved ${data.filename} to ${data.folder}`;
      $('downloadEdit').href=data.download_url;$('downloadEdit').download=data.filename;$('downloadEdit').hidden=false;
    } catch(err) {$('editorStatus').textContent=err.message;}
    finally {$('saveEdit').disabled=false;}
  }
  function open(selectedPhoto, token) {
    photo=selectedPhoto;csrf=token;revision++;clearTimeout(timer);releasePreview();
    $('editorTitle').textContent=`RAW Editor — ${photo.filename}`;
    $('editorImage').removeAttribute('src');$('editorImage').hidden=true;$('editorPreviewMessage').hidden=false;
    $('editorPreviewMessage').textContent='Preparing RAW preview…';$('editorStatus').textContent='';$('downloadEdit').hidden=true;
    applySettings(defaults);
    if(!$('editorDialog').open)$('editorDialog').showModal();
    renderPreview();
  }
  function close() { if($('editorDialog').open)$('editorDialog').close(); }

  for(const name of controls){
    const id='edit'+name[0].toUpperCase()+name.slice(1);
    $(id).addEventListener('input',()=>schedulePreview());
    $(id).addEventListener('change',()=>schedulePreview(100));
  }
  $('autoEdit').onclick=autoAdjust;
  $('resetEdit').onclick=()=>{applySettings(defaults);schedulePreview(50);};
  $('saveEdit').onclick=save;
  $('closeEditor').onclick=close;
  $('editorDialog').addEventListener('close',()=>{revision++;clearTimeout(timer);releasePreview();photo=null;});
  window.rawCatalogEditor={open,close};
})();
