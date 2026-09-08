from pathlib import Path

def replace(path, old, new):
    p=Path(path); text=p.read_text(encoding='utf-8')
    if old not in text: raise SystemExit(f'anchor missing: {old!r}')
    p.write_text(text.replace(old,new),encoding='utf-8')

replace('app/worker.py','from sqlalchemy import select, update, func\n','from sqlalchemy import select, update, func, delete, or_\n')
replace('app/worker.py',"def digest(value):\n",'''def purge_macos_garbage_rows():
    clauses = [Photo.filename.startswith('._', autoescape=True), Photo.filename == '.DS_Store']
    for directory in ('.AppleDouble', '__MACOSX', '.Spotlight-V100', '.Trashes', '.fseventsd'):
        clauses.append(Photo.path.contains('/' + directory + '/', autoescape=True))
    with Session.begin() as db:
        result = db.execute(delete(Photo).where(or_(*clauses)))
        removed = result.rowcount or 0
    if removed:
        log.info('Removed %s previously indexed macOS metadata rows', removed)
    return removed


def digest(value):
''')
replace('app/worker.py',"    parallelism = job_parallelism(job_id)\n\n    try:\n", "    parallelism = job_parallelism(job_id)\n    purge_macos_garbage_rows()\n\n    try:\n")
replace('app/statistics.py',"        mount = _clean_text(lo.get('mount')) or _clean_text(co.get('mount')) or _lens_mount(metadata, camera, lens)\n", "        mount = _clean_text(lo.get('mount')) or _lens_mount(metadata, camera, lens) or _clean_text(co.get('mount'))\n")
with Path('tests/test_statistics_technical.py').open('a',encoding='utf-8') as f:
    f.write('''\n\ndef test_purge_previously_indexed_macos_garbage():\n    from app.worker import purge_macos_garbage_rows\n    with Session.begin() as db:\n        db.add(Photo(path_hash=\"junk\", path=\"/photos/._junk.dng\", filename=\"._junk.dng\", size=1, mtime_ns=1, camera=\"Unknown\", lens=\"Unknown\"))\n        db.add(Photo(path_hash=\"good\", path=\"/photos/good.dng\", filename=\"good.dng\", size=1, mtime_ns=1, camera=\"Good\", lens=\"Good\"))\n    assert purge_macos_garbage_rows() == 1\n    with Session() as db:\n        assert len(list(db.scalars(select(Photo)))) == 1\n''')
