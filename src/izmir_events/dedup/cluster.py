"""Etkinlikleri kümeleyip tek kanonik kayda indirger."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

import structlog

from ..models import Category, Event
from ..util.money import Price
from ..util.text import clean_display_title, is_shouting
from .similarity import compare

log = structlog.get_logger(__name__)

# Bir etkinliğin bloklamaya katkı sağlayacağı azami gün sayısı: aylarca süren
# sergilerin her gününü ayrı bloka yazmak karşılaştırma sayısını patlatır.
MAX_BLOCK_DAYS = 3


class UnionFind:
    """Basit birleşim-bulma (path compression + union by size)."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))
        self._size = [1] * size

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._size[ra] < self._size[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        self._size[ra] += self._size[rb]

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for i in range(len(self._parent)):
            out[self.find(i)].append(i)
        return out


@dataclass
class DedupStats:
    """Tekilleştirme raporu."""

    input_count: int = 0
    output_count: int = 0
    comparisons: int = 0
    merged_pairs: int = 0
    examples: list[str] = field(default_factory=list)

    @property
    def removed(self) -> int:
        return self.input_count - self.output_count

    def summary(self) -> str:
        return (
            f"{self.input_count} kayıt -> {self.output_count} benzersiz etkinlik "
            f"({self.removed} tekrar birleştirildi, {self.comparisons} karşılaştırma)"
        )


def _blocks(events: list[Event]) -> dict[object, list[int]]:
    """Karşılaştırmayı O(n²)'den kurtaran bloklama.

    İki etkinlik ancak tarihleri kesişirse eşleşebildiği için, sadece
    aynı gün(ler)e denk gelen kayıtlar karşılaştırılır. Uzun süren
    etkinlikler ayrıca ortak bir "uzun" havuzunda karşılaştırılır.
    """
    blocks: dict[object, list[int]] = defaultdict(list)
    for idx, event in enumerate(events):
        span = (event.end - event.start).days
        if span > MAX_BLOCK_DAYS:
            # Uzun etkinlikler: ay bazlı blok + baş/son gün blokları
            blocks[("uzun", event.start.year, event.start.month)].append(idx)
            blocks[event.start].append(idx)
            blocks[event.end].append(idx)
            continue
        for offset in range(span + 1):
            blocks[event.start + timedelta(days=offset)].append(idx)
    return blocks


def _priority(event: Event, source_priority: dict[str, int]) -> int:
    return max((source_priority.get(k, 50) for k in event.sources), default=50)


def _pick_canonical(members: list[Event], source_priority: dict[str, int]) -> Event:
    """Kümedeki en iyi "temsilci" kaydı seçer.

    Öncelik: güvenilir kaynak > mekan bilgisi var > makul uzunlukta başlık.
    Reklam gibi çok uzun başlıklar ("Efsane sanatçı ... İzmir'de!") elenir.
    """

    def score(event: Event) -> tuple[int, int, int, int, int]:
        title_len = len(event.title)
        # 12-60 karakter arası başlıklar ideal.
        length_score = 2 if 12 <= title_len <= 60 else (1 if title_len <= 90 else 0)
        # Tamamı büyük harfle veya "| İzmir" kuyruğuyla yazılmış başlıklar
        # aynı bilgiyi taşısa da okunaklı değil; eşitlik bozulurken geri planda kalsın.
        tidy = 0 if (is_shouting(event.title) or "|" in event.title) else 1
        return (
            _priority(event, source_priority),
            tidy,
            1 if event.venue else 0,
            length_score,
            1 if event.image else 0,
        )

    return max(members, key=score)


def merge_cluster(members: list[Event], source_priority: dict[str, int]) -> Event:
    """Bir kümedeki kayıtları tek etkinliğe indirger.

    Kanonik kayıt başlığı/mekanı verir; diğer kayıtlar eksik alanları
    tamamlar. Fiyatta en bilgilendirici bilgi, tarihte en geniş aralık,
    kategoride "Diğer" olmayan ilk değer kazanır.
    """
    if len(members) == 1:
        return members[0].model_copy(update={"title": clean_display_title(members[0].title)})

    canonical = _pick_canonical(members, source_priority)
    others = [m for m in members if m is not canonical]
    ordered = [canonical, *sorted(others, key=lambda e: -_priority(e, source_priority))]

    sources: dict[str, str] = {}
    source_titles: dict[str, str] = {}
    for member in ordered:
        for key, url in member.sources.items():
            if key not in sources or (not sources[key] and url):
                sources[key] = url
        source_titles.update(member.source_titles)

    price = Price(
        is_free=canonical.is_free,
        min_amount=canonical.price_min,
        max_amount=canonical.price_max,
        raw=canonical.price_raw,
        needs_registration=canonical.needs_registration,
    )
    for member in others:
        price = price.merge(member.price)

    category = canonical.category
    if category is Category.OTHER:
        for member in others:
            if member.category is not Category.OTHER:
                category = member.category
                break

    # Tarih: kaynaklar farklı bitiş verebilir; en geniş aralığı al ama
    # başlangıcı kanonik kaydın günü olarak koru (en güvenilir kaynak).
    start = canonical.start
    end = max(m.end for m in members)
    if end < start:
        end = start
    start_time = canonical.start_time or next((m.start_time for m in ordered if m.start_time), None)

    first_seen = min((m.first_seen for m in members if m.first_seen), default=None)

    return Event(
        title=clean_display_title(canonical.title),
        start=start,
        end=end,
        start_time=start_time,
        venue=canonical.venue or next((m.venue for m in ordered if m.venue), None),
        category=category,
        is_free=price.is_free,
        price_min=price.min_amount,
        price_max=price.max_amount,
        price_raw=price.raw,
        needs_registration=price.needs_registration,
        description=canonical.description
        or next((m.description for m in ordered if m.description), None),
        image=canonical.image or next((m.image for m in ordered if m.image), None),
        sources=sources,
        source_titles=source_titles,
        first_seen=first_seen,
    )


def deduplicate(
    events: list[Event],
    *,
    threshold: float = 0.82,
    date_tolerance_days: int = 1,
    source_priority: dict[str, int] | None = None,
    collect_examples: int = 8,
) -> tuple[list[Event], DedupStats]:
    """Etkinlik listesini tekilleştirir.

    Döndürülen liste tarihe, sonra kaynak sayısına göre sıralıdır
    (çok kaynakta geçen etkinlik daha "gerçek" ve daha popülerdir).
    """
    stats = DedupStats(input_count=len(events))
    source_priority = source_priority or {}
    if len(events) <= 1:
        # Tek kayıtta da başlık temizliği uygulanmalı; merge_cluster'dan geçir.
        single = [merge_cluster(list(events), source_priority)] if events else []
        stats.output_count = len(single)
        return single, stats

    uf = UnionFind(len(events))
    compared: set[tuple[int, int]] = set()

    for members in _blocks(events).values():
        if len(members) < 2:
            continue
        for pos, i in enumerate(members):
            for j in members[pos + 1 :]:
                pair = (i, j) if i < j else (j, i)
                if pair in compared:
                    continue
                compared.add(pair)
                stats.comparisons += 1
                match = compare(events[i], events[j], date_tolerance_days=date_tolerance_days)
                if match.score >= threshold:
                    if uf.find(i) != uf.find(j):
                        stats.merged_pairs += 1
                        if len(stats.examples) < collect_examples:
                            stats.examples.append(
                                f"{events[i].title!r} ≡ {events[j].title!r} "
                                f"({match.score:.2f}: {match.reason})"
                            )
                    uf.union(i, j)

    merged: list[Event] = [
        merge_cluster([events[i] for i in idxs], source_priority) for idxs in uf.groups().values()
    ]
    merged.sort(key=lambda e: (e.start, -e.source_count, e.title))
    stats.output_count = len(merged)
    log.info(
        "dedup_done",
        **{"in": stats.input_count, "out": stats.output_count, "comparisons": stats.comparisons},
    )
    return merged, stats


def upcoming_only(events: list[Event], ref: date | None = None) -> list[Event]:
    """Bitmiş etkinlikleri ayıklar."""
    from ..util.dates import today

    ref = ref or today()
    return [e for e in events if e.end >= ref]
