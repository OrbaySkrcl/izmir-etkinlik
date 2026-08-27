"""Veritabanı bağlantısı ve oturum yönetimi."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import get_settings
from .models import Base

log = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _ensure_sqlite_dir(url: str) -> None:
    """SQLite dosyasının klasörü yoksa oluşturur (Railway volume için)."""
    if not url.startswith("sqlite"):
        return
    _, _, path = url.partition(":///")
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def get_engine(url: str | None = None) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        url = url or get_settings().database_url
        _ensure_sqlite_dir(url)
        kwargs: dict = {"echo": False, "future": True}
        if not url.startswith("sqlite"):
            kwargs |= {"pool_size": 5, "max_overflow": 5, "pool_pre_ping": True}
        _engine = create_async_engine(url, **kwargs)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
        log.info("db_engine_created", dialect=url.split("://")[0])
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Otomatik commit/rollback yapan oturum bağlamı."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db(url: str | None = None) -> None:
    """Tabloları oluşturur (yoksa)."""
    engine = get_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("db_ready")


async def dispose_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def reset_engine_for_tests(url: str) -> None:  # pragma: no cover - test yardımcısı
    global _engine, _session_factory
    _engine = None
    _session_factory = None
    get_engine(url)
