"""Veritabanı sorguları."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Category, Event
from ..util.dates import Bucket, bucket_bounds, today
from ..util.text import normalize_title, strip_accents, tr_lower
from .models import EventRow, ScrapeRun, SourceHealth, Subscriber, utcnow

log = structlog.get_logger(__name__)


# --- dönüştürücüler ----------------------------------------------------------


def to_row(event: Event) -> dict:
    return {
        "uid": event.uid(),
        "title": event.title[:300],
        "norm_title": event.norm_title[:300],
        "start": event.start,
        "end": event.end,
        "start_time": event.start_time,
        "venue": event.venue[:300] if event.venue else None,
        "category": event.category.value,
        "is_free": event.is_free,
        "price_min": event.price_min,
        "price_max": event.price_max,
        "price_raw": event.price_raw[:200],
        "needs_registration": event.needs_registration,
        "description": event.description,
        "image": event.image,
        "sources": event.sources,
        "source_titles": event.source_titles,
        "source_count": len(event.sources),
    }


def from_row(row: EventRow) -> Event:
    return Event(
        title=row.title,
        start=row.start,
        end=row.end,
        start_time=row.start_time,
        venue=row.venue,
        category=Category(row.category) if row.category else Category.OTHER,
        is_free=row.is_free,
        price_min=row.price_min,
        price_max=row.price_max,
        price_raw=row.price_raw or "",
        needs_registration=row.needs_registration,
        description=row.description,
        image=row.image,
        sources=dict(row.sources or {}),
        source_titles=dict(row.source_titles or {}),
        first_seen=row.first_seen,
    )


@dataclass
class UpsertResult:
    inserted: int = 0
    updated: int = 0
    new_uids: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.new_uids is None:
            self.new_uids = []


# --- etkinlikler -------------------------------------------------------------


async def upsert_events(session: AsyncSession, events: list[Event]) -> UpsertResult:
    """Etkinlikleri ekler/günceller ve hangilerinin yeni olduğunu bildirir.

    Var olan kayıtta ``first_seen`` korunur, ``last_seen`` tazelenir; böylece
    "bu etkinliği ilk kez görüyoruz" bildirimi yalnızca bir kez atılır.
    """
    result = UpsertResult()
    if not events:
        return result

    rows = [to_row(e) for e in events]
    uids = [r["uid"] for r in rows]
    existing = set(
        (await session.execute(select(EventRow.uid).where(EventRow.uid.in_(uids)))).scalars()
    )
    now = utcnow()

    for row in rows:
        if row["uid"] in existing:
            await session.execute(
                update(EventRow).where(EventRow.uid == row["uid"]).values(**row, last_seen=now)
            )
            result.updated += 1
        else:
            session.add(EventRow(**row, first_seen=now, last_seen=now, announced=False))
            result.inserted += 1
            result.new_uids.append(row["uid"])

    await session.flush()
    return result


async def get_events(
    session: AsyncSession,
    *,
    start: date | None = None,
    end: date | None = None,
    free_only: bool = False,
    categories: list[str] | None = None,
    query: str | None = None,
    min_sources: int = 0,
    limit: int = 100,
    offset: int = 0,
) -> list[Event]:
    """Filtrelere uyan etkinlikleri tarih sırasıyla döndürür.

    Çok günlü etkinlikler aralıkla kesişiyorsa dahil edilir (bir sergi
    "bu hafta" sorgusunda görünmeli).
    """
    stmt = select(EventRow)
    if start is not None:
        stmt = stmt.where(EventRow.end >= start)
    if end is not None:
        stmt = stmt.where(EventRow.start <= end)
    if free_only:
        stmt = stmt.where(EventRow.is_free.is_(True))
    if categories:
        stmt = stmt.where(EventRow.category.in_(categories))
    if min_sources > 0:
        stmt = stmt.where(EventRow.source_count >= min_sources)

    stmt = stmt.order_by(EventRow.start, EventRow.source_count.desc(), EventRow.title)
    # Metin araması Türkçe'ye duyarlı olmalı; SQL LIKE bunu yapamadığı için
    # aday kümesi çekilip Python tarafında filtrelenir.
    if query:
        stmt = stmt.limit(max(limit * 20, 500))
        rows = list((await session.execute(stmt)).scalars())
        needle = strip_accents(tr_lower(query)).strip()
        norm_needle = normalize_title(query, keep_core=False)
        matched = [
            r
            for r in rows
            if needle in strip_accents(tr_lower(f"{r.title} {r.venue or ''}"))
            or (norm_needle and norm_needle in r.norm_title)
        ]
        return [from_row(r) for r in matched[offset : offset + limit]]

    stmt = stmt.offset(offset).limit(limit)
    return [from_row(r) for r in (await session.execute(stmt)).scalars()]


async def get_events_for_bucket(
    session: AsyncSession, bucket: Bucket, *, ref: date | None = None, **kwargs
) -> list[Event]:
    """Bir tarih kovasına düşen etkinlikleri getirir."""
    start, end = bucket_bounds(bucket, ref)
    return await get_events(session, start=start, end=end, **kwargs)


async def get_new_events(
    session: AsyncSession, *, limit: int = 20, ref: date | None = None
) -> list[Event]:
    """Henüz duyurulmamış, gelecekteki etkinlikler."""
    ref = ref or today()
    stmt = (
        select(EventRow)
        .where(EventRow.announced.is_(False), EventRow.end >= ref)
        .order_by(EventRow.start)
        .limit(limit)
    )
    return [from_row(r) for r in (await session.execute(stmt)).scalars()]


async def mark_announced(session: AsyncSession, uids: list[str]) -> None:
    if uids:
        await session.execute(update(EventRow).where(EventRow.uid.in_(uids)).values(announced=True))


async def count_events(session: AsyncSession, *, ref: date | None = None) -> dict[str, int]:
    """Özet sayaçlar: toplam / gelecek / ücretsiz."""
    ref = ref or today()
    total = (await session.execute(select(func.count(EventRow.uid)))).scalar_one()
    upcoming = (
        await session.execute(select(func.count(EventRow.uid)).where(EventRow.end >= ref))
    ).scalar_one()
    free = (
        await session.execute(
            select(func.count(EventRow.uid)).where(EventRow.end >= ref, EventRow.is_free.is_(True))
        )
    ).scalar_one()
    multi = (
        await session.execute(
            select(func.count(EventRow.uid)).where(EventRow.end >= ref, EventRow.source_count > 1)
        )
    ).scalar_one()
    return {"toplam": total, "gelecek": upcoming, "ucretsiz": free, "cok_kaynakli": multi}


async def category_counts(session: AsyncSession, *, ref: date | None = None) -> dict[str, int]:
    ref = ref or today()
    stmt = (
        select(EventRow.category, func.count(EventRow.uid))
        .where(EventRow.end >= ref)
        .group_by(EventRow.category)
        .order_by(func.count(EventRow.uid).desc())
    )
    return {row[0]: row[1] for row in (await session.execute(stmt)).all()}


async def prune_stale_events(session: AsyncSession, *, days: int = 14) -> int:
    """Son taramalarda görülmeyen kayıtları siler.

    Bir kaynak etkinliği kaldırdığında veya ayrıştırma düzeltildiği için
    kayıt yeni bir kimlikle geldiğinde, eski satır aksi halde gelecek
    tarihli olduğu için sonsuza dek listede kalır.
    """
    if days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(delete(EventRow).where(EventRow.last_seen < cutoff))
    return int(getattr(result, "rowcount", 0) or 0)


async def delete_all_events(session: AsyncSession) -> int:
    """Tüm etkinlik kayıtlarını siler (elle tam yeniden tarama için)."""
    result = await session.execute(delete(EventRow))
    return int(getattr(result, "rowcount", 0) or 0)


async def prune_old_events(
    session: AsyncSession, *, keep_days: int = 30, ref: date | None = None
) -> int:
    """Bitmiş etkinlikleri belli bir süre sonra siler."""
    ref = ref or today()
    cutoff = ref - timedelta(days=keep_days)
    result = await session.execute(delete(EventRow).where(EventRow.end < cutoff))
    return int(getattr(result, "rowcount", 0) or 0)


# --- aboneler ----------------------------------------------------------------


async def get_subscriber(session: AsyncSession, chat_id: int) -> Subscriber | None:
    return await session.get(Subscriber, chat_id)


async def upsert_subscriber(
    session: AsyncSession, chat_id: int, *, title: str | None = None, **fields: Any
) -> Subscriber:
    sub = await session.get(Subscriber, chat_id)
    if sub is None:
        sub = Subscriber(chat_id=chat_id, title=title)
        session.add(sub)
    if title:
        sub.title = title
    for key, value in fields.items():
        setattr(sub, key, value)
    await session.flush()
    return sub


async def list_subscribers(
    session: AsyncSession, *, digest_only: bool = False, notify_only: bool = False
) -> list[Subscriber]:
    stmt = select(Subscriber).where(Subscriber.active.is_(True))
    if digest_only:
        stmt = stmt.where(Subscriber.digest_enabled.is_(True))
    if notify_only:
        stmt = stmt.where(Subscriber.notify_new.is_(True))
    return list((await session.execute(stmt)).scalars())


async def deactivate_subscriber(session: AsyncSession, chat_id: int) -> None:
    """Bot engellendiğinde aboneyi pasifleştirir (kaydı silmeden)."""
    await session.execute(
        update(Subscriber).where(Subscriber.chat_id == chat_id).values(active=False)
    )


# --- çalıştırma kayıtları ----------------------------------------------------


async def record_run(
    session: AsyncSession,
    *,
    raw_count: int,
    unique_count: int,
    new_count: int,
    duration: float,
    per_source: dict,
    errors: list[str],
) -> None:
    session.add(
        ScrapeRun(
            started_at=datetime.now(UTC) - timedelta(seconds=duration),
            finished_at=utcnow(),
            raw_count=raw_count,
            unique_count=unique_count,
            new_count=new_count,
            duration_seconds=duration,
            per_source=per_source,
            errors=errors[:20],
        )
    )


async def last_run(session: AsyncSession) -> ScrapeRun | None:
    stmt = select(ScrapeRun).order_by(ScrapeRun.id.desc()).limit(1)
    return (await session.execute(stmt)).scalars().first()


async def update_source_health(
    session: AsyncSession, key: str, *, count: int, strategy: str | None, error: str | None
) -> None:
    """Kaynak sağlığını günceller; üst üste başarısızlıklar sayılır."""
    stmt = select(SourceHealth).where(SourceHealth.source_key == key)
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        # Sütun varsayılanları INSERT anında uygulandığı için, flush öncesi
        # alanlar None kalır; sayaç aritmetiği bozulmasın diye açıkça veriyoruz.
        row = SourceHealth(source_key=key, consecutive_failures=0, last_count=0)
        session.add(row)
    row.last_run_at = utcnow()
    row.last_count = count
    row.last_strategy = strategy
    row.last_error = error
    if count > 0:
        row.last_ok_at = utcnow()
        row.consecutive_failures = 0
    else:
        row.consecutive_failures += 1


async def source_health(session: AsyncSession) -> list[SourceHealth]:
    stmt = select(SourceHealth).order_by(SourceHealth.source_key)
    return list((await session.execute(stmt)).scalars())
