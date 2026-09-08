from pathlib import Path


def replace(path, old, new):
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'anchor missing in {path}: {old!r}')
    p.write_text(text.replace(old, new), encoding='utf-8')

replace(
    'app/db.py',
    "    indexed_at: Mapped[datetime] = mapped_column(DateTime, default=now)\n",
    "    indexed_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)\n",
)

replace(
    'app/db.py',
    "        if not favorite_index:\n            connection.execute(text('CREATE INDEX ix_photos_favorite ON photos (favorite)'))\n",
    "        if not favorite_index:\n            connection.execute(text('CREATE INDEX ix_photos_favorite ON photos (favorite)'))\n        indexed_at_index = connection.scalar(text(\"\"\"\n            SELECT COUNT(*) FROM information_schema.STATISTICS\n            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'photos' AND INDEX_NAME = 'ix_photos_indexed_at'\n        \"\"\"))\n        if not indexed_at_index:\n            connection.execute(text('CREATE INDEX ix_photos_indexed_at ON photos (indexed_at)'))\n",
)

replace(
    'app/db.py',
    "        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_photos_favorite ON photos (favorite)'))\n",
    "        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_photos_favorite ON photos (favorite)'))\n        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_photos_indexed_at ON photos (indexed_at)'))\n",
)

replace(
    'app/statistics.py',
    "def _signature(db):\n    count, latest = db.execute(select(func.count(Photo.id), func.max(Photo.indexed_at))).one()\n    return f'{count}:{latest.isoformat() if latest else \"\"}:{reference_fingerprint(db)}'\n",
    "def _signature(db):\n    # Cache validation must stay cheap even with hundreds of thousands of photos.\n    # MAX(id) detects inserts and MAX(indexed_at) detects re-indexed rows; both are\n    # index lookups instead of a COUNT() scan over the whole catalog.\n    latest_id = db.scalar(select(func.max(Photo.id))) or 0\n    latest = db.scalar(select(func.max(Photo.indexed_at)))\n    return f'{latest_id}:{latest.isoformat() if latest else \"\"}:{reference_fingerprint(db)}'\n",
)

replace(
    'app/worker.py',
    "from app.storage import (cache_root, configured_thumbnail_folder, configured_preview_folder,\n                         configured_preview_edge, configured_preview_quality,\n                         resolve_thumbnail_root, resolve_preview_root)\n",
    "from app.storage import (cache_root, configured_thumbnail_folder, configured_preview_folder,\n                         configured_preview_edge, configured_preview_quality,\n                         resolve_thumbnail_root, resolve_preview_root)\nfrom app.statistics import CACHE_KEY as STATISTICS_CACHE_KEY\n",
)

replace(
    'app/worker.py',
    "    with Session.begin() as db:\n        result = db.execute(delete(Photo).where(or_(*clauses)))\n        removed = result.rowcount or 0\n",
    "    with Session.begin() as db:\n        result = db.execute(delete(Photo).where(or_(*clauses)))\n        removed = result.rowcount or 0\n        # Deleting a non-highest photo would not change MAX(id), so explicitly\n        # invalidate the statistics payload whenever cleanup removes rows.\n        if removed:\n            cached = db.get(Setting, STATISTICS_CACHE_KEY)\n            if cached:\n                db.delete(cached)\n",
)
