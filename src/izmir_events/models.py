"""Uygulama genelinde kullanılan veri modelleri."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .util.dates import Bucket, EventDates, classify, format_dates, parse_dates
from .util.money import Price, parse_price
from .util.text import clean_whitespace, normalize_title, normalize_venue, slugify


class Category(StrEnum):
    """Etkinlik türü. Kaynaktan gelen serbest metin buraya eşlenir."""

    CONCERT = "konser"
    THEATRE = "tiyatro"
    EXHIBITION = "sergi"
    FESTIVAL = "festival"
    CINEMA = "sinema"
    STANDUP = "stand_up"
    KIDS = "cocuk"
    WORKSHOP = "atolye"
    TALK = "soylesi"
    OPERA = "opera_bale"
    SPORTS = "spor"
    OTHER = "diger"

    @property
    def label(self) -> str:
        return {
            Category.CONCERT: "Konser",
            Category.THEATRE: "Tiyatro",
            Category.EXHIBITION: "Sergi",
            Category.FESTIVAL: "Festival",
            Category.CINEMA: "Sinema",
            Category.STANDUP: "Stand-up",
            Category.KIDS: "Çocuk",
            Category.WORKSHOP: "Atölye",
            Category.TALK: "Söyleşi",
            Category.OPERA: "Opera & Bale",
            Category.SPORTS: "Spor",
            Category.OTHER: "Diğer",
        }[self]

    @property
    def emoji(self) -> str:
        return {
            Category.CONCERT: "🎵",
            Category.THEATRE: "🎭",
            Category.EXHIBITION: "🖼",
            Category.FESTIVAL: "🎪",
            Category.CINEMA: "🎬",
            Category.STANDUP: "🎤",
            Category.KIDS: "🧸",
            Category.WORKSHOP: "🛠",
            Category.TALK: "💬",
            Category.OPERA: "🩰",
            Category.SPORTS: "⚽",
            Category.OTHER: "📌",
        }[self]


# Kaynaklardan gelen serbest metin kategorileri -> Category
_CATEGORY_KEYWORDS: list[tuple[tuple[str, ...], Category]] = [
    (("konser", "concert", "muzik", "müzik", "canlı müzik", "dj"), Category.CONCERT),
    (("tiyatro", "oyun", "theatre", "sahne sanat"), Category.THEATRE),
    (("sergi", "exhibition", "galeri"), Category.EXHIBITION),
    (("festival", "fest"), Category.FESTIVAL),
    (("sinema", "film", "gösterim", "cinema"), Category.CINEMA),
    (("stand", "komedi", "comedy", "gösteri sanat"), Category.STANDUP),
    (("çocuk", "cocuk", "kids", "aile"), Category.KIDS),
    (("atölye", "atolye", "workshop", "kurs", "eğitim"), Category.WORKSHOP),
    (("söyleşi", "soylesi", "panel", "konferans", "seminer", "imza"), Category.TALK),
    (("opera", "bale", "ballet"), Category.OPERA),
    (("spor", "maç", "mac", "futbol", "basketbol"), Category.SPORTS),
]


# Mekan adından çıkarılabilen zayıf ipuçları. Sadece güçlü sinyaller
# (kategori metni, başlık) sonuç vermediğinde kullanılır.
_VENUE_KEYWORDS: list[tuple[tuple[str, ...], Category]] = [
    (("opera", "bale"), Category.OPERA),
    (("sinema", "cinema", "sinemasi"), Category.CINEMA),
    (("galeri", "müze", "muze", "sanat galerisi"), Category.EXHIBITION),
    (("arena", "açıkhava", "acikhava", "stadyum", "amfi"), Category.CONCERT),
    (("tiyatro", "sahne", "sahnesi"), Category.THEATRE),
]


def guess_category(*texts: str | None, venue: str | None = None) -> Category:
    """Etkinlik türünü tahmin eder.

    Önce kategori metni ve başlık gibi güçlü sinyallere bakılır; sonuç
    çıkmazsa mekan adı zayıf ipucu olarak değerlendirilir ("Arena İzmir"
    büyük olasılıkla konser).
    """
    blob = " ".join(t for t in texts if t).lower()
    for keywords, category in _CATEGORY_KEYWORDS:
        if any(k in blob for k in keywords):
            return category
    if venue:
        venue_blob = venue.lower()
        for keywords, category in _VENUE_KEYWORDS:
            if any(k in venue_blob for k in keywords):
                return category
    return Category.OTHER


class RawEvent(BaseModel):
    """Bir kaynaktan çıkarılan ham etkinlik kaydı (henüz ayrıştırılmamış)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source: str
    title: str
    url: str | None = None
    date_text: str | None = None
    time_text: str | None = None
    venue: str | None = None
    price_text: str | None = None
    category_text: str | None = None
    image: str | None = None
    description: str | None = None
    # JSON-LD'den gelen makine-okur alanlar (varsa ayrıştırmayı atlar)
    start_iso: str | None = None
    end_iso: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def _clean_title(cls, v: str) -> str:
        v = clean_whitespace(v)
        if not v:
            raise ValueError("başlık boş olamaz")
        return v


class Event(BaseModel):
    """Ayrıştırılmış, tekilleştirmeye hazır etkinlik."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    title: str
    start: date
    end: date
    start_time: time | None = None
    venue: str | None = None
    category: Category = Category.OTHER
    is_free: bool = False
    price_min: float | None = None
    price_max: float | None = None
    price_raw: str = ""
    needs_registration: bool = False
    description: str | None = None
    image: str | None = None
    # Kaynak izleri: {kaynak_anahtarı: url}
    sources: dict[str, str] = Field(default_factory=dict)
    source_titles: dict[str, str] = Field(default_factory=dict)
    first_seen: datetime | None = None

    # --- türetilmiş alanlar --------------------------------------------------

    @property
    def dates(self) -> EventDates:
        return EventDates(self.start, self.end, self.start_time, is_range=self.end > self.start)

    @property
    def price(self) -> Price:
        return Price(
            is_free=self.is_free,
            min_amount=self.price_min,
            max_amount=self.price_max,
            raw=self.price_raw,
            needs_registration=self.needs_registration,
        )

    @property
    def norm_title(self) -> str:
        return normalize_title(self.title)

    @property
    def norm_venue(self) -> str:
        return normalize_venue(self.venue)

    @property
    def source_count(self) -> int:
        return len(self.sources)

    def bucket(self, ref: date | None = None) -> Bucket:
        return classify(self.dates, ref)

    def date_label(self, ref: date | None = None) -> str:
        return format_dates(self.dates, ref=ref)

    def primary_url(self) -> str | None:
        """Gösterilecek tek link: kaynak öncelik sırasına göre ilk dolu URL."""
        for url in self.sources.values():
            if url:
                return url
        return None

    def uid(self) -> str:
        """Kararlı kimlik: normalize başlık + başlangıç tarihi + mekan.

        Aynı etkinlik farklı çalıştırmalarda aynı uid'i almalı ki
        "yeni etkinlik" bildirimleri doğru çalışsın.
        """
        key = f"{self.norm_title}|{self.start.isoformat()}|{self.norm_venue}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    def slug(self) -> str:
        return slugify(f"{self.title}-{self.start.isoformat()}")


def build_event(
    raw: RawEvent, *, free_by_default: bool = False, ref: date | None = None
) -> Event | None:
    """``RawEvent`` -> ``Event``. Tarih çıkarılamazsa ``None`` döner.

    Tarihsiz kayıt kullanıcıya hiçbir şey ifade etmediği için elenir; bu
    eleme sayısı pipeline istatistiklerinde raporlanır.
    """
    date_blob = " ".join(p for p in (raw.start_iso, raw.date_text, raw.time_text) if p) or None
    dates = parse_dates(date_blob, ref=ref)
    if dates is None:
        return None
    if raw.end_iso:
        end_dates = parse_dates(raw.end_iso, ref=ref)
        if end_dates and end_dates.start > dates.end:
            dates = EventDates(dates.start, end_dates.start, dates.start_time, is_range=True)

    price = parse_price(raw.price_text, free_by_default=free_by_default)
    url = raw.url or ""

    return Event(
        title=clean_whitespace(raw.title),
        start=dates.start,
        end=dates.end,
        start_time=dates.start_time,
        venue=clean_whitespace(raw.venue) if raw.venue else None,
        category=guess_category(raw.category_text, raw.title, raw.description, venue=raw.venue),
        is_free=price.is_free,
        price_min=price.min_amount,
        price_max=price.max_amount,
        price_raw=price.raw,
        needs_registration=price.needs_registration,
        description=clean_whitespace(raw.description)[:600] if raw.description else None,
        image=raw.image,
        sources={raw.source: url},
        source_titles={raw.source: clean_whitespace(raw.title)},
    )
