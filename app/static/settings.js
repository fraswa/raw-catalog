'use strict';
const $ = id => document.getElementById(id);
let csrf = '', timer, lastJobSignature = '';
const nf = new Intl.NumberFormat();

async function api(url, options = {}) {
  const response = await fetch(url, {...options, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...(options.headers || {})}});
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401) location.href = '/';
    throw new Error(result.error || `Request failed (${response.status})`);
  }
  return result;
}
function showError(err) { $('settingsError').textContent = err.message; $('settingsError').hidden = false; }
function clearError() { $('settingsError').hidden = true; }
function formatBytes(value) {
  if (!value) return '0 B';
  const units = ['B','KB','MB','GB','TB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}
function setActionsDisabled(disabled) {
  for (const id of ['saveFolder','thumbnailFolder','rebuildThumbnails','purgeThumbnails',
                    'savePreviewFolder','previewFolder','previewEdge','rebuildPreviews','purgePreviews']) $(id).disabled = disabled;
}
function showThumbnailSettings(data) {
  $('cacheRoot').textContent = data.cache_root;
  $('externalRoot').textContent = data.external_root;
  $('thumbnailFolder').value = data.folder;
  $('thumbnailPath').textContent = data.path;
  $('thumbnailFiles').textContent = nf.format(data.files);
  $('thumbnailBytes').textContent = formatBytes(data.bytes);
  $('eligiblePhotos').textContent = nf.format(data.eligible_photos);
  $('estimatedBytes').textContent = `≈ ${formatBytes(data.estimated_bytes)}`;
  $('estimateBasis').textContent = `Estimate uses ${formatBytes(data.estimated_per_thumbnail)} per thumbnail (${data.estimate_basis}).`;
}
function showPreviewSettings(data) {
  $('previewFolder').value = data.folder;
  $('previewEdge').value = String(data.preview_edge);
  $('previewPath').textContent = data.path;
  $('previewFiles').textContent = nf.format(data.files);
  $('previewBytes').textContent = formatBytes(data.bytes);
  $('previewEligiblePhotos').textContent = nf.format(data.eligible_photos);
  $('previewEstimatedBytes').textContent = `≈ ${formatBytes(data.estimated_bytes)}`;
  $('previewEstimateBasis').textContent = `Target max edge ${nf.format(data.preview_edge)} px. Estimate uses ${formatBytes(data.estimated_per_preview)} per preview (${data.estimate_basis}).`;
}
async function loadSettings() {
  const [thumbnails, previews] = await Promise.all([api('/api/settings/thumbnails'), api('/api/settings/previews')]);
  showThumbnailSettings(thumbnails); showPreviewSettings(previews);
  setActionsDisabled(thumbnails.active || previews.active);
  return {thumbnails, previews};
}
async function pollJob() {
  try {
    const data = await api('/api/scan'), job = data.scan;
    const active = job && ['queued','running'].includes(job.state);
    setActionsDisabled(!!active);
    $('settingsState').textContent = active ? job.state : 'Ready';
    $('cancelJob').hidden = !active; $('cancelJob').disabled = !!job?.cancel;
    $('cancelJob').textContent = job?.cancel ? 'Cancelling…' : 'Cancel current job';
    if (job) {
      $('jobMessage').textContent = job.message || job.state;
      $('jobCounts').textContent = `${nf.format(job.discovered)} found · ${nf.format(job.indexed)} processed · ${nf.format(job.errors)} errors`;
      $('jobPath').textContent = job.current_path || '';
    } else {
      $('jobMessage').textContent = 'No active scan or rebuild.'; $('jobCounts').textContent = ''; $('jobPath').textContent = '';
    }
    const signature = job ? `${job.id}:${job.state}` : '';
    if (lastJobSignature && signature !== lastJobSignature && !active) await loadSettings();
    lastJobSignature = signature;
  } catch (err) { showError(err); }
  finally { timer = setTimeout(pollJob, 3000); }
}
$('thumbnailFolderForm').onsubmit = async event => {
  event.preventDefault(); clearError(); $('saveFolder').disabled = true;
  $('thumbnailActionMessage').textContent = 'Changing thumbnail folder…';
  try {
    const data = await api('/api/settings/thumbnails', {method:'PUT', body:JSON.stringify({folder:$('thumbnailFolder').value.trim()})});
    showThumbnailSettings(data);
    $('thumbnailActionMessage').textContent = 'Thumbnail destination changed. Rebuild to populate the new location.';
    await loadSettings();
  } catch (err) { $('thumbnailActionMessage').textContent = ''; showError(err); }
  finally { $('saveFolder').disabled = false; }
};
$('previewFolderForm').onsubmit = async event => {
  event.preventDefault(); clearError(); $('savePreviewFolder').disabled = true;
  $('previewActionMessage').textContent = 'Saving preview settings…';
  try {
    const data = await api('/api/settings/previews', {method:'PUT', body:JSON.stringify({folder:$('previewFolder').value.trim(), preview_edge:Number($('previewEdge').value)})});
    showPreviewSettings(data);
    $('previewActionMessage').textContent = 'Preview destination/size saved. Rebuild previews to apply the selected size to all photos.';
    await loadSettings();
  } catch (err) { $('previewActionMessage').textContent = ''; showError(err); }
  finally { $('savePreviewFolder').disabled = false; }
};
$('rebuildThumbnails').onclick = async () => {
  if (!confirm('Rebuild all thumbnails from indexed originals?')) return;
  clearError(); $('rebuildThumbnails').disabled = true; $('thumbnailActionMessage').textContent = 'Thumbnail rebuild queued…';
  try { await api('/api/cache/thumbnails/rebuild',{method:'POST'}); clearTimeout(timer); await pollJob(); }
  catch (err) { $('thumbnailActionMessage').textContent=''; showError(err); $('rebuildThumbnails').disabled=false; }
};
$('rebuildPreviews').onclick = async () => {
  if (!confirm(`Rebuild all previews at ${$('previewEdge').value}px maximum edge?`)) return;
  clearError(); $('rebuildPreviews').disabled = true; $('previewActionMessage').textContent = 'Preview rebuild queued…';
  try { await api('/api/cache/previews/rebuild',{method:'POST'}); clearTimeout(timer); await pollJob(); }
  catch (err) { $('previewActionMessage').textContent=''; showError(err); $('rebuildPreviews').disabled=false; }
};
$('purgeThumbnails').onclick = async () => {
  if (!confirm('Delete generated thumbnails in the current and legacy cache locations?')) return;
  clearError(); $('purgeThumbnails').disabled=true; $('thumbnailActionMessage').textContent='Purging thumbnails…';
  try { const data=await api('/api/cache/thumbnails',{method:'DELETE'}); $('thumbnailActionMessage').textContent=`Removed ${nf.format(data.removed)} thumbnails (${formatBytes(data.bytes_removed)}).`; await loadSettings(); }
  catch(err){$('thumbnailActionMessage').textContent='';showError(err);} finally{$('purgeThumbnails').disabled=false;}
};
$('purgePreviews').onclick = async () => {
  if (!confirm('Delete generated previews in the current and legacy cache locations?')) return;
  clearError(); $('purgePreviews').disabled=true; $('previewActionMessage').textContent='Purging previews…';
  try { const data=await api('/api/cache/previews',{method:'DELETE'}); $('previewActionMessage').textContent=`Removed ${nf.format(data.removed)} previews (${formatBytes(data.bytes_removed)}).`; await loadSettings(); }
  catch(err){$('previewActionMessage').textContent='';showError(err);} finally{$('purgePreviews').disabled=false;}
};
$('cancelJob').onclick = async () => { clearError(); try { await api('/api/scan/cancel',{method:'POST'}); clearTimeout(timer); await pollJob(); } catch(err){showError(err);} };
$('signOut').onclick = async () => { try { await api('/api/logout',{method:'POST'}); } finally { location.href='/'; } };
(async()=>{try{const state=await api('/api/session');csrf=state.csrf;if(!state.authenticated){location.href='/';return;}$('settingsApp').hidden=false;await loadSettings();pollJob();}catch(err){showError(err);$('settingsApp').hidden=false;}})();
