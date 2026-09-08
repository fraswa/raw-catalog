import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Text, Integer, BigInteger, DateTime, JSON, Index, Boolean, text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Photo(Base):
    __tablename__ = 'photos'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path_hash: Mapped[str] = mapped_column(String(64), unique=True)
    path: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    mtime_ns: Mapped[int] = mapped_column(BigInteger)
    camera: Mapped[str] = mapped_column(String(190), default='Unknown camera', index=True)
    lens: Mapped[str] = mapped_column(String(190), default='Unknown lens', index=True)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    cache_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preview_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    __table_args__ = (Index('ix_camera_lens_id', 'camera', 'lens', 'id'),)


class Scan(Base):
    __tablename__ = 'scans'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(20), default='queued', index=True)
    force: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel: Mapped[bool] = mapped_column(Boolean, default=False)
    discovered: Mapped[int] = mapped_column(Integer, default=0)
    indexed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    current_path: Mapped[str] = mapped_column(Text, default='')
    message: Mapped[str] = mapped_column(Text, default='Waiting for indexer')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Setting(Base):
    __tablename__ = 'settings'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    # MariaDB TEXT is limited to 64 KiB. Expanded statistics can easily exceed that,
    # so use LONGTEXT on MySQL/MariaDB while retaining normal TEXT elsewhere.
    value: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), 'mysql'), default='')


engine = create_engine(os.environ.get('DATABASE_URL', 'sqlite:////tmp/raw-catalog-dev.db'), pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)


def _upgrade_mysql_schema():
    if engine.dialect.name != 'mysql':
        return
    with engine.begin() as connection:
        data_type = connection.scalar(text("""
            SELECT DATA_TYPE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'settings'
              AND COLUMN_NAME = 'value'
        """))
        if data_type and str(data_type).lower() != 'longtext':
            connection.execute(text('ALTER TABLE settings MODIFY value LONGTEXT NOT NULL'))
        favorite_column = connection.scalar(text("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'photos' AND COLUMN_NAME = 'favorite'
        """))
        if not favorite_column:
            connection.execute(text('ALTER TABLE photos ADD COLUMN favorite BOOLEAN NOT NULL DEFAULT 0'))
        favorite_index = connection.scalar(text("""
            SELECT COUNT(*) FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'photos' AND INDEX_NAME = 'ix_photos_favorite'
        """))
        if not favorite_index:
            connection.execute(text('CREATE INDEX ix_photos_favorite ON photos (favorite)'))
        indexed_at_index = connection.scalar(text("""
            SELECT COUNT(*) FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'photos' AND INDEX_NAME = 'ix_photos_indexed_at'
        """))
        if not indexed_at_index:
            connection.execute(text('CREATE INDEX ix_photos_indexed_at ON photos (indexed_at)'))


def _upgrade_sqlite_schema():
    if engine.dialect.name != 'sqlite':
        return
    with engine.begin() as connection:
        columns = {row[1] for row in connection.execute(text('PRAGMA table_info(photos)'))}
        if columns and 'favorite' not in columns:
            connection.execute(text('ALTER TABLE photos ADD COLUMN favorite BOOLEAN NOT NULL DEFAULT 0'))
        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_photos_favorite ON photos (favorite)'))
        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_photos_indexed_at ON photos (indexed_at)'))


def init_db():
    Base.metadata.create_all(engine)
    _upgrade_mysql_schema()
    _upgrade_sqlite_schema()
