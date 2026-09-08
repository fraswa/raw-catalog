from pathlib import Path

js = Path('app/static/statistics.js')
text = js.read_text(encoding='utf-8')
old = "function top(rows,n=2){return (rows||[]).slice(0,n).map(x=>x.value).join(', ')||null}"
new = "function topValues(rows,n=2){return (rows||[]).slice(0,n).map(x=>x.value).join(', ')||null}"
if old not in text:
    raise SystemExit('statistics top helper anchor not found')
text = text.replace(old, new)
text = text.replace("top(d.mounts)", "topValues(d.mounts)")
if "function top(" in text:
    raise SystemExit('global top helper still present')
js.write_text(text, encoding='utf-8')

html = Path('app/static/statistics.html')
text = html.read_text(encoding='utf-8')
if 'statistics.js?v=7' not in text:
    raise SystemExit('statistics asset version anchor not found')
html.write_text(text.replace('statistics.js?v=7', 'statistics.js?v=8'), encoding='utf-8')
