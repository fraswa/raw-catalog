from pathlib import Path


def replace_once(path, old, new):
    path = Path(path)
    text = path.read_text()
    if old not in text:
        raise RuntimeError(f'expected text not found in {path}: {old[:180]!r}')
    path.write_text(text.replace(old, new, 1))


def append_once(path, marker, content):
    path = Path(path)
    text = path.read_text()
    if marker not in text:
        path.write_text(text + content)


# Statistics payload must contain the complete camera/lens lists. The browser
# keeps the first 20 visible by default and expands them on demand.
replace_once('app/statistics.py', "CACHE_KEY = 'statistics_cache_v3'", "CACHE_KEY = 'statistics_cache_v4'")
replace_once(
    'app/statistics.py',
    "        'cameras': _top(cameras), 'lenses': _top(lenses), 'focal_lengths': _top(focals), 'apertures': _top(apertures),\n",
    "        'cameras': _top(cameras, None), 'lenses': _top(lenses, None), 'focal_lengths': _top(focals), 'apertures': _top(apertures),\n",
)
replace_once(
    'app/statistics.py',
    "            'lenses': _top(camera_lenses[camera]),\n",
    "            'lenses': _top(camera_lenses[camera], None),\n",
)

# Add explicit checkboxes below the Camera and Lens usage lists.
replace_once(
    'app/static/statistics.html',
    '<div class="stats-card"><div class="card-heading"><p class="eyebrow">CAMERAS</p><h2>Camera usage</h2><p>Click a camera name to restrict all other statistics to that camera. Hover or keyboard-focus a camera for its catalog reference card.</p></div><div id="cameraBars" class="bar-list"></div></div>\n',
    '<div class="stats-card"><div class="card-heading"><p class="eyebrow">CAMERAS</p><h2>Camera usage</h2><p>Click a camera name to restrict all other statistics to that camera. Hover or keyboard-focus a camera for its catalog reference card.</p></div><div id="cameraBars" class="bar-list"></div><label class="list-toggle" for="showAllCameras"><input id="showAllCameras" type="checkbox"><span id="showAllCamerasLabel">Show all cameras</span></label></div>\n',
)
replace_once(
    'app/static/statistics.html',
    '<div class="stats-card"><div class="card-heading"><p class="eyebrow">LENSES</p><h2 id="lensHeading">Lens usage</h2><p>Click a lens to open the matching Library view. Hover or keyboard-focus a lens for cameras, mount, focal length and aperture facts.</p></div><div id="lensBars" class="bar-list"></div></div>\n',
    '<div class="stats-card"><div class="card-heading"><p class="eyebrow">LENSES</p><h2 id="lensHeading">Lens usage</h2><p>Click a lens to open the matching Library view. Hover or keyboard-focus a lens for cameras, mount, focal length and aperture facts.</p></div><div id="lensBars" class="bar-list"></div><label class="list-toggle" for="showAllLenses"><input id="showAllLenses" type="checkbox"><span id="showAllLensesLabel">Show all lenses</span></label></div>\n',
)

# Frontend list helpers. They are scope-aware, so the lens checkbox also shows
# every lens when the Statistics page is drilled down to one camera.
replace_once(
    'app/static/statistics.js',
    "function coverageText(value,total,prefix){return `${prefix}: ${nf.format(value||0)} of ${nf.format(total||0)} photos (${pct(value||0,total||0)}%)`;}\n",
    "function coverageText(value,total,prefix){return `${prefix}: ${nf.format(value||0)} of ${nf.format(total||0)} photos (${pct(value||0,total||0)}%)`;}\nconst LIST_PREVIEW_LIMIT=20;\nfunction visibleRows(rows,checkboxId){return $(checkboxId).checked?(rows||[]):(rows||[]).slice(0,LIST_PREVIEW_LIMIT);}\nfunction syncListToggle(checkboxId,labelId,noun,count){const checkbox=$(checkboxId),label=$(labelId),expandable=count>LIST_PREVIEW_LIMIT;checkbox.disabled=!expandable;if(!expandable)checkbox.checked=false;label.textContent=expandable?`Show all ${noun} (${nf.format(count)})`:`All ${noun} shown (${nf.format(count)})`;}\nfunction renderCameraUsage(){if(!archiveData)return;const rows=archiveData.cameras||[];syncListToggle('showAllCameras','showAllCamerasLabel','cameras',rows.length);renderBars('cameraBars',visibleRows(rows,'showAllCameras'),{camera:true,total:archiveData.total_photos,referenceType:'camera'});}\nfunction renderLensUsage(){if(!archiveData)return;const detail=selectedCamera?archiveData.camera_breakdowns?.[selectedCamera]:null;const rows=detail?.lenses||archiveData.lenses||[];syncListToggle('showAllLenses','showAllLensesLabel','lenses',rows.length);renderBars('lensBars',visibleRows(rows,'showAllLenses'),{libraryParam:'lens',includeCamera:!!detail,total:detail?.total_photos||archiveData.total_photos,referenceType:'lens'});}\n",
)
replace_once(
    'app/static/statistics.js',
    "  renderBars('cameraBars',archiveData.cameras,{camera:true,total:archiveData.total_photos,referenceType:'camera'});\n  renderBars('lensBars',detail.lenses,{libraryParam:'lens',includeCamera:true,total:detail.total_photos,referenceType:'lens'});\n",
    "  renderCameraUsage();\n  renderLensUsage();\n",
)
replace_once(
    'app/static/statistics.js',
    "  renderBars('cameraBars',data.cameras,{camera:true,total:data.total_photos,referenceType:'camera'});\n  renderBars('lensBars',data.lenses,{libraryParam:'lens',total:data.total_photos,referenceType:'lens'});\n",
    "  renderCameraUsage();\n  renderLensUsage();\n",
)
replace_once(
    'app/static/statistics.js',
    "$('clearCameraScope').onclick=clearCamera;\n$('refreshStats').onclick=()=>load(true);\n",
    "$('clearCameraScope').onclick=clearCamera;\n$('showAllCameras').onchange=renderCameraUsage;\n$('showAllLenses').onchange=renderLensUsage;\n$('refreshStats').onclick=()=>load(true);\n",
)

append_once(
    'app/static/statistics.css',
    '.list-toggle{',
    "\n.list-toggle{display:flex;align-items:center;gap:.55rem;margin:1rem 0 0;padding-top:.85rem;border-top:1px solid var(--line);color:var(--muted);font-size:.78rem;cursor:pointer}.list-toggle input{width:auto;margin:0;accent-color:var(--accent)}.list-toggle:has(input:disabled){opacity:.55;cursor:default}\n",
)

append_once(
    'tests/test_statistics_technical.py',
    'test_statistics_exposes_complete_camera_and_lens_lists',
    '''\n\ndef test_statistics_exposes_complete_camera_and_lens_lists(client):\n    with Session.begin() as db:\n        for index in range(25):\n            db.add(Photo(\n                path_hash=f'full-list-{index}', path=f'/photos/full-{index}.dng',\n                filename=f'full-{index}.dng', size=1, mtime_ns=index + 1,\n                camera=f'Camera {index:02d}', lens=f'Lens {index:02d}',\n                metadata_json={'Make': 'Test'},\n            ))\n\n    data = client.get('/api/statistics?refresh=1').json\n    assert len(data['cameras']) == 25\n    assert len(data['lenses']) == 25\n    assert data['cameras'][0]['count'] == 1\n    assert data['lenses'][0]['count'] == 1\n''',
)
