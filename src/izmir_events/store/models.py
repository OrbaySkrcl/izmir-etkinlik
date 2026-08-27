"""Veritabanı şeması (SQLAlchemy 2.0)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class EventRow(Base):
    """Tekilleştirilmiş etkinlik.

    ``uid`` normalize başlık + tarih + mekandan türetilir; böylece aynı
    etkinlik her taramada aynı satıra denk gelir ve "yeni mi?" sorusu
    güvenilir biçimde cevaplanır.
    """

    __tablename__ = "events"

    uid: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    norm_title: Mapped[str] = mapped_column(String(300), index=True, default="")
    start: Mapped[date] = mapped_column(Date, index=True)
    end: Mapped[date] = mapped_column(Date, index=True)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    venue: Mapped[str | None] = mapped_column(String(300), nullable=True)
    category: Mapped[str] = mapped_column(String(32), index=True, default="diger")
    is_free: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    price_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_raw: Mapped[str] = mapped_column(String(200), default="")
    needs_registration: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources: Mapped[dict] = mapped_column(JSON, default=dict)
    source_titles: Mapped[dict] = mapped_column(JSON, default=dict)
    source_count: Mapped[int] = mapped_column(Integer, default=1, index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Aboneye duyurusu yapıldı mı?
    announced: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Subscriber(Base):
    """Telegram aboneleri ve bülten tercihleri."""

    __tablename__ = "subscribers"

    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    digest_hour: Mapped[int] = mapped_column(Integer, default=9)
    notify_new: Mapped[bool] = mapped_column(Boolean, default=True)
    free_only: Mapped[bool] = mapped_column(Boolean, default=False)
    # Boş liste = tüm kategoriler
    categories: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScrapeRun(Base):
    """Her tarama çalıştırmasının kaydı (sağlık takibi için)."""

    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_count: Mapped[int] = mapped_column(Integer, default=0)
    unique_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    per_source: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list] = mapped_column(JSON, default=list)


class SourceHealth(Base):
    """Kaynak başına son durum: sessizce bozulan scraper'ları yakalamak için."""

    __tablename__ = "source_health"
    __table_args__ = (UniqueConstraint("source_key", name="uq_source_health_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String(64), index=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_count: Mapped[int] = mapped_column(Integer, default=0)
    last_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
