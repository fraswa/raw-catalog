from pathlib import Path


def replace(path, old, new):
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'anchor missing in {path}: {old!r}')
    p.write_text(text.replace(old, new), encoding='utf-8')

# Never hide the entire page behind JavaScript. If JS is blocked or crashes, the
# user must still get a visible loading/error surface instead of a blank page.
replace('app/static/statistics.html',
        '<script defer src="/static/statistics.js?v=6"></script></head>\n<body><div id="statisticsApp" hidden>\n',
        '<script defer src="/static/statistics.js?v=7"></script></head>\n<body><div id="statisticsApp">\n<div id="statisticsBootError" class="error" role="alert">Statistics JavaScript has not started. If this message remains visible, the browser did not load the current statistics script.</div>\n')

# Install a visible runtime error handler as early as possible, then hide the boot
# warning once this script is known to be executing.
replace('app/static/statistics.js',
        "let csrf='',archiveData=null,selectedCamera='',selectedMount='',referenceState=null,referenceHideTimer=null,referenceEditing=false;\n",
        "let csrf='',archiveData=null,selectedCamera='',selectedMount='',referenceState=null,referenceHideTimer=null,referenceEditing=false;\n"
        "const bootError=$('statisticsBootError');if(bootError)bootError.hidden=true;\n"
        "function fatalStatisticsError(message){const app=$('statisticsApp');if(app)app.hidden=false;const loading=$('statisticsLoading');if(loading)loading.hidden=true;const box=$('statisticsError');if(box){box.textContent=message||'Statistics failed to start';box.hidden=false;}}\n"
        "window.addEventListener('error',event=>fatalStatisticsError(event.message||'Statistics JavaScript error'));\n"
        "window.addEventListener('unhandledrejection',event=>fatalStatisticsError(event.reason?.message||String(event.reason||'Statistics request failed')));\n")

# Make the normal error helper itself tolerant of a stale/partial DOM.
replace('app/static/statistics.js',
        "function showError(e){$('statisticsError').textContent=e.message;$('statisticsError').hidden=false}\n",
        "function showError(e){fatalStatisticsError(e?.message||String(e||'Statistics error'))}\n")

# Root is already visible; keep auth startup defensive instead of relying on a
# particular HTML generation being in the browser cache.
replace('app/static/statistics.js',
        "(async()=>{try{const s=await api('/api/session');csrf=s.csrf;if(!s.authenticated){location.href='/';return}$('statisticsApp').hidden=false;await load()}catch(e){$('statisticsApp').hidden=false;$('statisticsLoading').hidden=true;showError(e)}})();",
        "(async()=>{try{const s=await api('/api/session');csrf=s.csrf;if(!s.authenticated){location.href='/';return}await load()}catch(e){showError(e)}})();")
