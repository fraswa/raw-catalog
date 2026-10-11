'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const defaults = {exposure:0,contrast:0,highlights:0,shadows:0,temperature:6500,tint:0,saturation:0,black:0,white:255,denoise:0};
  const controls = ['exposure','contrast','highlights','shadows','temperature','tint','saturation','black','white','denoise'];
  let photo = null, csrf = '', timer = null, objectUrl = '', revision = 0, previewController = null;
  let editorSession = 0, exportRevision = 0, standardPrompt = '', promptRequest = null;

  function invalidateChatgptExport() {
    exportRevision++;
    $('downloadChatgpt').hidden=true;
    $('downloadChatgpt').removeAttribute('href');
    $('chatgptStatus').textContent='';
  }
  function rememberPrompt() {
    try { localStorage.setItem('rawCatalog.cosplayPrompt', $('chatgptPrompt').value); } catch {}
  }
  async function loadChatgptPrompt() {
    if(standardPrompt)return;
    if(!promptRequest)promptRequest=json('/api/editor/chatgpt');
    const session=editorSession;
    try {
      const data=await promptRequest;
      if(session!==editorSession)return;
      standardPrompt=data.prompt;
      let custom='';
      try { custom=localStorage.getItem('rawCatalog.cosplayPrompt')||''; } catch {}
      $('chatgptPrompt').value=custom||standardPrompt;
      $('chatgptPrompt').disabled=false;
      $('copyChatgptPrompt').disabled=false;
      $('resetChatgptPrompt').disabled=false;
    } catch(err) {
      if(session===editorSession)$('chatgptStatus').textContent=`Prompt could not load: ${err.message}. Close and reopen the editor to retry.`;
    } finally { promptRequest=null; }
  }
  async function copyChatgptPrompt() {
    const prompt=$('chatgptPrompt').value.trim();
    if(!prompt){$('chatgptStatus').textContent='Enter an editing prompt first.';return;}
    const session=editorSession;
    try {
      await navigator.clipboard.writeText(prompt);
      if(session===editorSession)$('chatgptStatus').textContent='Prompt copied. Attach the downloaded JPEG in ChatGPT, paste the prompt, and send.';
    } catch {
      if(session!==editorSession)return;
      $('chatgptPrompt').focus();$('chatgptPrompt').select();
      $('chatgptStatus').textContent='Prompt selected. Press Ctrl+C (or Command+C) to copy it, then paste it in ChatGPT with the JPEG.';
    }
  }
  async function exportChatgpt() {
    if(!photo)return;
    const selected=photo, session=editorSession, current=exportRevision;
    $('exportChatgpt').disabled=true;$('chatgptMegapixels').disabled=true;
    $('downloadChatgpt').hidden=true;
    $('chatgptStatus').textContent='Preparing JPEG from RAW…';
    try {
      const data=await json(`/api/photos/${selected.id}/editor/chatgpt-export`,{
        method:'POST',body:JSON.stringify({settings:settings(),megapixels:Number($('chatgptMegapixels').value)})
      });
      if(session!==editorSession)return;
      if(current!==exportRevision){
        $('chatgptStatus').textContent='JPEG saved in Edits using the previous adjustments. Prepare again to include your latest changes.';
        return;
      }
      $('downloadChatgpt').href=data.download_url;$('downloadChatgpt').download=data.filename;$('downloadChatgpt').hidden=false;
      $('chatgptStatus').textContent=`Saved ${data.width} × ${data.height} (${data.megapixels} MP), ${(data.bytes/1000000).toFixed(1)} MB. Download the JPEG and copy the prompt below.`;
    } catch(err) {
      if(session===editorSession)$('chatgptStatus').textContent=`JPEG export failed: ${err.message}`;
    } finally {
      if(session===editorSession){$('exportChatgpt').disabled=false;$('chatgptMegapixels').disabled=false;}
    }
  }

  function settings() {
    return {
      exposure:Number($('editExposure').value),
      contrast:Number($('editContrast').value),
      highlights:Number($('editHighlights').value),
      shadows:Number($('editShadows').value),
      temperature:Number($('editTemperature').value),
      tint:Number($('editTint').value),
      saturation:Number($('editSaturation').value),
      black:Number($('editBlack').value),
      white:Number($('editWhite').value),
      denoise:Number($('editDenoise').value)
    };
  }
  function signed(value) { return `${value>0?'+':''}${Math.round(value)}`; }
  function displayValues() {
    const s=settings();
    $('editExposureValue').textContent=`${s.exposure>0?'+':''}${s.exposure.toFixed(1)} EV`;
    $('editContrastValue').textContent=signed(s.contrast);
    $('editHighlightsValue').textContent=signed(s.highlights);
    $('editShadowsValue').textContent=signed(s.shadows);
    $('editTemperatureValue').textContent=`${Math.round(s.temperature)} K`;
    $('editTintValue').textContent=signed(s.tint);
    $('editSaturationValue').textContent=signed(s.saturation);
    $('editBlackValue').textContent=String(Math.round(s.black));
    $('editWhiteValue').textContent=String(Math.round(s.white));
    $('editDenoiseValue').textContent=`${Math.round(s.denoise)}%`;
  }
  function applySettings(value) {
    const s={...defaults,...(value||{})};
    $('editExposure').value=s.exposure;$('editContrast').value=s.contrast;$('editHighlights').value=s.highlights;$('editShadows').value=s.shadows;
    $('editTemperature').value=s.temperature;$('editTint').value=s.tint;$('editSaturation').value=s.saturation;
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
    invalidateChatgptExport();
    displayValues();clearTimeout(timer);timer=setTimeout(renderPreview,delay);
    $('downloadEdit').hidden=true;
  }
  async function autoAdjust() {
    if(!photo)return;
    $('autoEdit').disabled=true;$('editorStatus').textContent='Analyzing RAW…';
    try {
      const data=await json(`/api/photos/${photo.id}/editor/auto`,{method:'POST',body:'{}'});
      applySettings(data.settings);invalidateChatgptExport();await renderPreview();
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
    editorSession++;invalidateChatgptExport();
    $('exportChatgpt').disabled=false;$('chatgptMegapixels').disabled=false;
    photo=selectedPhoto;csrf=token;revision++;clearTimeout(timer);if(previewController)previewController.abort();releasePreview();
    $('editorTitle').textContent=`RAW Editor — ${photo.filename}`;
    $('editorImage').removeAttribute('src');$('editorImage').hidden=true;$('editorPreviewMessage').hidden=false;
    $('editorPreviewMessage').textContent='Preparing RAW preview…';$('editorStatus').textContent='';$('downloadEdit').hidden=true;
    applySettings(defaults);
    if(!$('editorDialog').open)$('editorDialog').showModal();
    loadChatgptPrompt();
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
  $('exportChatgpt').onclick=exportChatgpt;
  $('copyChatgptPrompt').onclick=copyChatgptPrompt;
  $('resetChatgptPrompt').onclick=()=>{$('chatgptPrompt').value=standardPrompt;rememberPrompt();};
  $('chatgptPrompt').addEventListener('input',rememberPrompt);
  $('chatgptMegapixels').addEventListener('change',invalidateChatgptExport);
  $('closeEditor').onclick=close;
  $('editorDialog').addEventListener('close',()=>{
    if($('editorDialog').open)return;
    editorSession++;revision++;clearTimeout(timer);if(previewController)previewController.abort();previewController=null;releasePreview();photo=null;
  });
  window.rawCatalogEditor={open,close};
})();
