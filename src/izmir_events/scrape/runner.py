"""Kaynak koşucusu: bir kaynağın sayfalarını indirip etkinlikleri çıkarır."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlencode, urlparse, urlunparse

import structlog

from ..config import SourceConfig
from ..models import Event, RawEvent, build_event
from ..util.dates import is_date_only, today
from ..util.text import strip_accents, tr_lower
from .extractors import (
    extract_heuristic,
    extract_jsonld,
    extract_nextdata,
    extract_selectors,
)
from .http import HttpClient

log = structlog.get_logger(__name__)

# Etkinlik olmayan sayfa gürültüsü (menü, kampanya, kurumsal linkler).
_JUNK_TITLE_RE = re.compile(
    r"^(anasayfa|iletisim|hakkimizda|giris yap|uye ol|sepet|kvkk|cerez|gizlilik|"
    r"tumunu gor|devami|daha fazla|kategoriler|arama|filtrele|sonraki|onceki|"
    r"tum etkinlikler|bilet al|satin al|kampanya|blog|haberler|yardim|sss)$"
)

# İzmir'e ait olduğunu gösteren ipuçları (city_filter açık kaynaklarda kullanılır).
_IZMIR_HINTS = (
    "izmir",
    "konak",
    "alsancak",
    "karsiyaka",
    "bornova",
    "cesme",
    "urla",
    "buca",
    "gaziemir",
    "balcova",
    "narlidere",
    "bayrakli",
    "torbali",
    "kulturpark",
    "alacati",
    "foca",
    "seferihisar",
    "menemen",
    "odemis",
    "bergama",
    "tire",
    "selcuk",
    "dikili",
    "aliaga",
)


@dataclass
class SourceResult:
    """Bir kaynağın tek çalıştırmasının sonucu ve teşhis bilgisi."""

    key: str
    name: str
    events: list[Event] = field(default_factory=list)
    strategy: str | None = None
    pages_fetched: int = 0
    raw_count: int = 0
    dropped_no_date: int = 0
    dropped_filtered: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.events)

    def summary(self) -> str:
        if self.errors and not self.events:
            return f"{self.name}: HATA ({'; '.join(self.errors[:2])})"
        return (
            f"{self.name}: {len(self.events)} etkinlik "
            f"[{self.strategy or '-'}] "
            f"({self.pages_fetched} sayfa, {self.dropped_no_date} tarihsiz, "
            f"{self.dropped_filtered} filtrelendi)"
        )


def paged_urls(source: SourceConfig) -> list[str]:
    """Sayfalama ayarına göre indirilecek tüm URL'leri üretir."""
    urls = list(source.urls)
    pagination = source.pagination
    if not pagination or pagination.max_pages <= 1:
        return urls

    expanded: list[str] = []
    for base in urls:
        expanded.append(base)
        for page in range(pagination.start + 1, pagination.start + pagination.max_pages):
            if pagination.template:
                expanded.append(pagination.template.format(page=page))
                continue
            parts = urlparse(base)
            query = parts.query
            extra = urlencode({pagination.param: page})
            new_query = f"{query}&{extra}" if query else extra
            expanded.append(urlunparse(parts._replace(query=new_query)))
    # Şablonlu sayfalama tüm listeleme URL'leri için tekrar üretilmesin.
    seen: set[str] = set()
    unique: list[str] = []
    for url in expanded:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def _is_junk(title: str) -> bool:
    """Etkinlik adı olmayan başlıkları eler.

    İki tür gürültü var: site menüsü ("Anasayfa", "Sepet") ve tarih
    elemanının başlık sanılması ("29 Ağustos 2026"). İkincisi kullanıcıya
    tarihi kendi adı olan sahte etkinlikler olarak görünür.
    """
    flat = strip_accents(tr_lower(title)).strip(" .:-|")
    if len(flat) < 3 or len(flat) > 180:
        return True
    if _JUNK_TITLE_RE.match(flat):
        return True
    return is_date_only(title)


def _mentions_izmir(raw: RawEvent) -> bool:
    blob = strip_accents(
        tr_lower(" ".join(p for p in (raw.title, raw.venue, raw.description, raw.url) if p))
    )
    return any(hint in blob for hint in _IZMIR_HINTS)


def run_extractors(html: str, url: str, source: SourceConfig) -> tuple[list[RawEvent], str | None]:
    """Stratejileri sırayla dener, en çok kayıt döndüreni seçer.

    "İlk çalışan" yerine "en verimli" strateji seçilir: bazı siteler tek bir
    öne çıkan etkinlik için JSON-LD yayınlarken listenin tamamı HTML'dedir.
    """
    attempts: list[tuple[str, list[RawEvent]]] = []
    for strategy in source.strategies:
        found: list[RawEvent]
        try:
            match strategy:
                case "jsonld":
                    found = extract_jsonld(html, url, source.key)
                case "nextdata":
                    found = extract_nextdata(html, url, source.key)
                case "selectors":
                    found = extract_selectors(html, url, source.key, source.selectors)
                case "heuristic":
                    found = extract_heuristic(html, url, source.key)
                case _:
                    continue
        except Exception as exc:  # tek strateji patlarsa diğerleri denensin
            log.warning("strategy_failed", source=source.key, strategy=strategy, error=str(exc))
            continue
        if found:
            attempts.append((strategy, found))
            # Yapılandırılmış veri bol miktarda sonuç verdiyse gerisini deneme.
            if strategy in ("jsonld", "nextdata") and len(found) >= 5:
                break

    if not attempts:
        return [], None
    best_strategy, best_found = max(attempts, key=lambda pair: len(pair[1]))
    return best_found, best_strategy


async def scrape_source(
    client: HttpClient, source: SourceConfig, *, ref: date | None = None
) -> SourceResult:
    """Bir kaynağı baştan sona tarar."""
    ref = ref or today()
    result = SourceResult(key=source.key, name=source.name)
    urls = paged_urls(source)

    raws: list[RawEvent] = []
    strategies_used: list[str] = []

    for fetched in await client.fetch_all(urls, extra_headers=source.headers or None):
        if not fetched.ok:
            result.errors.append(f"{fetched.url}: {fetched.error or 'boş yanıt'}")
            continue
        result.pages_fetched += 1
        found, strategy = run_extractors(fetched.text, fetched.url, source)
        if strategy:
            strategies_used.append(strategy)
        raws.extend(found)

    result.raw_count = len(raws)
    if strategies_used:
        result.strategy = max(set(strategies_used), key=strategies_used.count)

    seen_keys: set[str] = set()
    for raw in raws:
        if _is_junk(raw.title):
            result.dropped_filtered += 1
            continue
        if source.city_filter and not _mentions_izmir(raw):
            result.dropped_filtered += 1
            continue
        if source.default_category and not raw.category_text:
            raw = raw.model_copy(update={"category_text": source.default_category})

        event = build_event(raw, free_by_default=source.free_by_default, ref=ref)
        if event is None:
            result.dropped_no_date += 1
            continue
        # Geçmiş etkinlikleri taşıma (çok günlü olup devam edenler kalır).
        if event.end < ref:
            result.dropped_filtered += 1
            continue
        key = event.uid()
        if key in seen_keys:  # aynı kaynağın farklı sayfalarındaki tekrarlar
            continue
        seen_keys.add(key)
        result.events.append(event)

    log.info(
        "source_done",
        source=source.key,
        events=len(result.events),
        strategy=result.strategy,
        pages=result.pages_fetched,
        errors=len(result.errors),
    )
    return result
