"""Toplama hattı: tara -> tekilleştir -> kaydet."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import structlog

from .config import Settings, SourceConfig, enabled_sources, get_settings
from .dedup.cluster import DedupStats, deduplicate
from .models import Event
from .scrape.http import HttpClient
from .scrape.runner import SourceResult, scrape_source
from .store import repo
from .store.db import session_scope
from .util.dates import today

log = structlog.get_logger(__name__)


@dataclass
class PipelineResult:
    """Bir toplama turunun tam sonucu."""

    events: list[Event] = field(default_factory=list)
    sources: list[SourceResult] = field(default_factory=list)
    dedup: DedupStats | None = None
    new_uids: list[str] = field(default_factory=list)
    inserted: int = 0
    updated: int = 0
    duration: float = 0.0

    @property
    def raw_count(self) -> int:
        return sum(len(s.events) for s in self.sources)

    @property
    def errors(self) -> list[str]:
        return [f"{s.key}: {e}" for s in self.sources for e in s.errors]

    @property
    def failed_sources(self) -> list[SourceResult]:
        return [s for s in self.sources if not s.ok]

    def report(self) -> str:
        """İnsan-okur özet (CLI ve /durum komutu için)."""
        lines = [
            f"Toplam {self.raw_count} kayıt -> {len(self.events)} benzersiz etkinlik "
            f"({self.duration:.1f} sn)",
            f"Yeni: {self.inserted} | Güncellenen: {self.updated}",
            "",
            "Kaynaklar:",
        ]
        for source in self.sources:
            mark = "✓" if source.ok else "✗"
            lines.append(f"  {mark} {source.summary()}")
        if self.dedup:
            lines += ["", f"Tekilleştirme: {self.dedup.summary()}"]
            if self.dedup.examples:
                lines.append("Birleştirilen örnekler:")
                lines += [f"  · {ex}" for ex in self.dedup.examples[:5]]
        return "\n".join(lines)


def _source_priority(sources: list[SourceConfig]) -> dict[str, int]:
    return {s.key: s.priority for s in sources}


async def collect(
    settings: Settings | None = None,
    *,
    sources: list[SourceConfig] | None = None,
    ref: date | None = None,
    use_cache: bool = True,
) -> PipelineResult:
    """Tüm kaynakları tarar ve tekilleştirilmiş etkinlik listesi döndürür.

    Veritabanına yazmaz; saf toplama adımıdır (test etmesi kolay olsun diye).
    """
    settings = settings or get_settings()
    sources = sources if sources is not None else enabled_sources()
    ref = ref or today()
    started = time.monotonic()
    result = PipelineResult()

    cache_dir = Path(".http_cache") if use_cache and settings.http_cache_ttl_minutes else None
    async with HttpClient(
        user_agent=settings.user_agent,
        timeout=settings.http_timeout,
        max_retries=settings.http_max_retries,
        concurrency=settings.http_concurrency,
        delay_seconds=settings.http_delay_seconds,
        cache_dir=cache_dir,
        cache_ttl_seconds=settings.http_cache_ttl_minutes * 60,
        respect_robots=settings.respect_robots,
    ) as client:
        for source in sources:
            try:
                result.sources.append(await scrape_source(client, source, ref=ref))
            except Exception as exc:  # bir kaynak çökerse tur devam etsin
                log.exception("source_crashed", source=source.key)
                result.sources.append(
                    SourceResult(
                        key=source.key, name=source.name, errors=[f"beklenmeyen hata: {exc}"]
                    )
                )

    all_events = [e for s in result.sources for e in s.events]
    merged, stats = deduplicate(
        all_events,
        threshold=settings.dedup_threshold,
        date_tolerance_days=settings.dedup_date_tolerance_days,
        source_priority=_source_priority(sources),
    )
    result.events = merged
    result.dedup = stats
    result.duration = time.monotonic() - started
    return result


async def collect_and_store(
    settings: Settings | None = None,
    *,
    sources: list[SourceConfig] | None = None,
    ref: date | None = None,
    use_cache: bool = True,
    prune: bool = True,
) -> PipelineResult:
    """Toplar ve veritabanına yazar; yeni etkinliklerin uid'lerini döndürür."""
    settings = settings or get_settings()
    result = await collect(settings, sources=sources, ref=ref, use_cache=use_cache)

    async with session_scope() as session:
        upsert = await repo.upsert_events(session, result.events)
        result.inserted = upsert.inserted
        result.updated = upsert.updated
        result.new_uids = upsert.new_uids

        for source in result.sources:
            await repo.update_source_health(
                session,
                source.key,
                count=len(source.events),
                strategy=source.strategy,
                error="; ".join(source.errors[:3]) or None,
            )

        await repo.record_run(
            session,
            raw_count=result.raw_count,
            unique_count=len(result.events),
            new_count=result.inserted,
            duration=result.duration,
            per_source={s.key: len(s.events) for s in result.sources},
            errors=result.errors,
        )

        if prune:
            removed = await repo.prune_old_events(session)
            if removed:
                log.info("pruned_old_events", count=removed)
            # Kaynaktan kalkmış veya ayrıştırma düzeldiği için kimliği değişmiş
            # kayıtlar aksi halde gelecek tarihli oldukları için hiç silinmez.
            stale = await repo.prune_stale_events(session, days=settings.prune_stale_days)
            if stale:
                log.info("pruned_stale_events", count=stale)

    log.info(
        "pipeline_done",
        raw=result.raw_count,
        unique=len(result.events),
        new=result.inserted,
        seconds=round(result.duration, 1),
    )
    return result
