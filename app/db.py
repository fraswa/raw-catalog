import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Text, Integer, BigInteger, DateTime, JSON, Index, Boolean
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
    indexed_at: Mapped[datetime] = mapped_column(DateTime, default=now)
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
    value: Mapped[str] = mapped_column(Text, default='')


engine = create_engine(os.environ.get('DATABASE_URL', 'sqlite:////tmp/raw-catalog-dev.db'), pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)


def init_db():
    Base.metadata.create_all(engine)
