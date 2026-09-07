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

function showError(err) {
  $('settingsError').textContent = err.message;
  $('settingsError').hidden = false;
}

function clearError() { $('settingsError').hidden = true; }

function formatBytes(value) {
  if (!value) return '0 B';
  const units = ['B','KB','MB','GB','TB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function setActionsDisabled(disabled) {
  $('saveFolder').disabled = disabled;
  $('thumbnailFolder').disabled = disabled;
  $('rebuildThumbnails').disabled = disabled;
  $('purgeThumbnails').disabled = disabled;
}

async function loadSettings() {
  const data = await api('/api/settings/thumbnails');
  $('cacheRoot').textContent = data.cache_root;
  $('thumbnailFolder').value = data.folder;
  $('thumbnailPath').textContent = data.path;
  $('thumbnailFiles').textContent = nf.format(data.files);
  $('thumbnailBytes').textContent = formatBytes(data.bytes);
  $('eligiblePhotos').textContent = nf.format(data.eligible_photos);
  $('estimatedBytes').textContent = `≈ ${formatBytes(data.estimated_bytes)}`;
  $('estimateBasis').textContent = `Estimate uses ${formatBytes(data.estimated_per_thumbnail)} per thumbnail (${data.estimate_basis}).`;
  setActionsDisabled(data.active);
  return data;
}

async function pollJob() {
  try {
    const data = await api('/api/scan');
    const job = data.scan;
    const active = job && ['queued','running'].includes(job.state);
    setActionsDisabled(!!active);
    $('settingsState').textContent = active ? job.state : 'Ready';
    $('cancelJob').hidden = !active;
    $('cancelJob').disabled = !!job?.cancel;
    $('cancelJob').textContent = job?.cancel ? 'Cancelling…' : 'Cancel current job';
    if (job) {
      $('jobMessage').textContent = job.message || job.state;
      $('jobCounts').textContent = `${nf.format(job.discovered)} found · ${nf.format(job.indexed)} processed · ${nf.format(job.errors)} errors`;
      $('jobPath').textContent = job.current_path || '';
    } else {
      $('jobMessage').textContent = 'No active scan or rebuild.';
      $('jobCounts').textContent = '';
      $('jobPath').textContent = '';
    }
    const signature = job ? `${job.id}:${job.state}` : '';
    if (lastJobSignature && signature !== lastJobSignature && !active) await loadSettings();
    lastJobSignature = signature;
  } catch (err) {
    showError(err);
  } finally {
    timer = setTimeout(pollJob, 3000);
  }
}

$('thumbnailFolderForm').onsubmit = async event => {
  event.preventDefault();
  clearError();
  $('saveFolder').disabled = true;
  $('actionMessage').textContent = 'Changing thumbnail folder…';
  try {
    const data = await api('/api/settings/thumbnails', {method:'PUT', body:JSON.stringify({folder:$('thumbnailFolder').value.trim()})});
    $('thumbnailFolder').value = data.folder;
    $('thumbnailPath').textContent = data.path;
    $('actionMessage').textContent = 'Thumbnail folder changed. Existing thumbnails in the previous folder are not moved; rebuild to populate the new folder.';
    await loadSettings();
  } catch (err) {
    $('actionMessage').textContent = '';
    showError(err);
  } finally {
    $('saveFolder').disabled = false;
  }
};

$('rebuildThumbnails').onclick = async () => {
  if (!window.confirm('Rebuild all thumbnails from indexed originals? Full previews and metadata will be left unchanged.')) return;
  clearError();
  $('rebuildThumbnails').disabled = true;
  $('actionMessage').textContent = 'Thumbnail rebuild queued…';
  try {
    await api('/api/cache/thumbnails/rebuild', {method:'POST'});
    clearTimeout(timer);
    await pollJob();
  } catch (err) {
    $('actionMessage').textContent = '';
    showError(err);
    $('rebuildThumbnails').disabled = false;
  }
};

$('purgeThumbnails').onclick = async () => {
  if (!window.confirm('Delete all generated thumbnails? Originals and full previews will not be touched.')) return;
  clearError();
  $('purgeThumbnails').disabled = true;
  $('actionMessage').textContent = 'Purging thumbnails…';
  try {
    const data = await api('/api/cache/thumbnails', {method:'DELETE'});
    $('actionMessage').textContent = `Removed ${nf.format(data.removed)} thumbnails (${formatBytes(data.bytes_removed)}).`;
    await loadSettings();
  } catch (err) {
    $('actionMessage').textContent = '';
    showError(err);
  } finally {
    $('purgeThumbnails').disabled = false;
  }
};

$('cancelJob').onclick = async () => {
  clearError();
  try {
    await api('/api/scan/cancel', {method:'POST'});
    clearTimeout(timer);
    await pollJob();
  } catch (err) { showError(err); }
};

$('signOut').onclick = async () => {
  try {
    await api('/api/logout', {method:'POST'});
  } finally {
    location.href = '/';
  }
};

(async () => {
  try {
    const state = await api('/api/session');
    csrf = state.csrf;
    if (!state.authenticated) { location.href = '/'; return; }
    $('settingsApp').hidden = false;
    await loadSettings();
    pollJob();
  } catch (err) {
    showError(err);
    $('settingsApp').hidden = false;
  }
})();
