"""Türkçe tarih/saat ayrıştırma ve tarih kovaları (bugün / bu hafta / …).

Kaynaklar tarihi çok farklı biçimlerde veriyor:

    "12 Eylül 2026"            "12 Eylül Cumartesi"
    "12 - 15 Eylül 2026"       "12 Eylül - 3 Ekim 2026"
    "12.09.2026 21:00"         "2026-09-12T21:00:00+03:00"
    "Bugün 20:30"              "Yarın"
    "12 Eyl"                   "Cumartesi 21.00"

Hepsi ``EventDates`` (başlangıç/bitiş + saat bilgisi var mı) yapısına indirgenir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

IZMIR_TZ = ZoneInfo("Europe/Istanbul")

MONTHS: dict[str, int] = {
    "ocak": 1,
    "oca": 1,
    "sub": 2,
    "şub": 2,
    "subat": 2,
    "şubat": 2,
    "mart": 3,
    "mar": 3,
    "nisan": 4,
    "nis": 4,
    "mayis": 5,
    "mayıs": 5,
    "may": 5,
    "haziran": 6,
    "haz": 6,
    "temmuz": 7,
    "tem": 7,
    "agustos": 8,
    "ağustos": 8,
    "agu": 8,
    "ağu": 8,
    "eylul": 9,
    "eylül": 9,
    "eyl": 9,
    "ekim": 10,
    "eki": 10,
    "kasim": 11,
    "kasım": 11,
    "kas": 11,
    "aralik": 12,
    "aralık": 12,
    "ara": 12,
    # İngilizce (bazı kaynaklar karışık kullanıyor)
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

WEEKDAYS: dict[str, int] = {
    "pazartesi": 0,
    "pzt": 0,
    "salı": 1,
    "sali": 1,
    "sal": 1,
    "çarşamba": 2,
    "carsamba": 2,
    "çar": 2,
    "car": 2,
    "çrş": 2,
    "perşembe": 3,
    "persembe": 3,
    "per": 3,
    "prş": 3,
    "cuma": 4,
    "cum": 4,
    "cumartesi": 5,
    "cmt": 5,
    "cts": 5,
    "pazar": 6,
    "paz": 6,
}

WEEKDAY_NAMES_TR = [
    "Pazartesi",
    "Salı",
    "Çarşamba",
    "Perşembe",
    "Cuma",
    "Cumartesi",
    "Pazar",
]
MONTH_NAMES_TR = [
    "",
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
]

_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_WEEKDAY_ALT = "|".join(sorted(WEEKDAYS, key=len, reverse=True))


class Bucket(StrEnum):
    """Kullanıcıya sunulan tarih kovaları."""

    TODAY = "bugun"
    TOMORROW = "yarin"
    THIS_WEEK = "bu_hafta"
    WEEKEND = "hafta_sonu"
    NEXT_WEEK = "gelecek_hafta"
    THIS_MONTH = "bu_ay"
    LATER = "ileride"
    PAST = "gecmis"

    @property
    def label(self) -> str:
        return {
            Bucket.TODAY: "Bugün",
            Bucket.TOMORROW: "Yarın",
            Bucket.THIS_WEEK: "Bu Hafta",
            Bucket.WEEKEND: "Bu Hafta Sonu",
            Bucket.NEXT_WEEK: "Gelecek Hafta",
            Bucket.THIS_MONTH: "Bu Ay",
            Bucket.LATER: "İleriki Tarihler",
            Bucket.PAST: "Geçmiş",
        }[self]


@dataclass(frozen=True, slots=True)
class EventDates:
    """Bir etkinliğin tarih aralığı.

    ``start`` her zaman dolu. ``end`` çok günlü etkinliklerde (sergi, festival)
    dolu olur; tek günlük etkinlikte ``start`` ile aynıdır.
    """

    start: date
    end: date
    start_time: time | None = None
    is_range: bool = False

    @property
    def multi_day(self) -> bool:
        return self.end > self.start

    def overlaps(self, other: EventDates, *, tolerance_days: int = 0) -> bool:
        """İki tarih aralığı (tolerans payıyla) kesişiyor mu?"""
        delta = timedelta(days=tolerance_days)
        return self.start - delta <= other.end and other.start - delta <= self.end

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end


def today(tz: ZoneInfo = IZMIR_TZ) -> date:
    return datetime.now(tz).date()


def now(tz: ZoneInfo = IZMIR_TZ) -> datetime:
    return datetime.now(tz)


# --- Ayrıştırma --------------------------------------------------------------

_ISO_RE = re.compile(
    r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"
    r"(?:[T ](?P<hh>\d{2}):(?P<mm>\d{2}))?"
)
_TIME_RE = re.compile(r"\b(?P<hh>[0-2]?\d)[:.](?P<mm>[0-5]\d)\b")
_NUMERIC_DATE_RE = re.compile(r"\b(?P<d>[0-3]?\d)[./-](?P<m>[01]?\d)[./-](?P<y>\d{4}|\d{2})\b")
# "12 Eylül 2026" / "12 Eyl"
_DMY_RE = re.compile(
    rf"\b(?P<d>[0-3]?\d)\s*(?:\.|\s)\s*(?P<mon>{_MONTH_ALT})\b\s*(?P<y>\d{{4}})?",
    re.IGNORECASE,
)
# "12 - 15 Eylül 2026"  (tek ay, iki gün)
_RANGE_SAME_MONTH_RE = re.compile(
    rf"\b(?P<d1>[0-3]?\d)\s*[-–—/]\s*(?P<d2>[0-3]?\d)\s+(?P<mon>{_MONTH_ALT})\b\s*(?P<y>\d{{4}})?",
    re.IGNORECASE,
)
# "12 Eylül - 3 Ekim 2026"
_RANGE_CROSS_MONTH_RE = re.compile(
    rf"\b(?P<d1>[0-3]?\d)\s+(?P<mon1>{_MONTH_ALT})\s*(?P<y1>\d{{4}})?\s*[-–—]\s*"
    rf"(?P<d2>[0-3]?\d)\s+(?P<mon2>{_MONTH_ALT})\s*(?P<y2>\d{{4}})?",
    re.IGNORECASE,
)
_WEEKDAY_RE = re.compile(rf"\b(?P<wd>{_WEEKDAY_ALT})\b", re.IGNORECASE)

_RELATIVE = {
    "bugün": 0,
    "bugun": 0,
    "today": 0,
    "yarın": 1,
    "yarin": 1,
    "tomorrow": 1,
    "öbür gün": 2,
    "obur gun": 2,
    "ertesi gün": 2,
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _year_for(month: int, day: int, ref: date, explicit: int | None) -> int:
    """Yıl belirtilmemişse en yakın gelecekteki yılı seçer.

    Aralık'ta "5 Ocak" görülürse gelecek yıl kastediliyordur; 60 günlük
    geriye tolerans, yeni biten etkinliklerin yanlışlıkla bir yıl ileri
    atılmasını engeller.
    """
    if explicit:
        return explicit + 2000 if explicit < 100 else explicit
    try:
        candidate = date(ref.year, month, day)
    except ValueError:  # 29 Şubat gibi
        return ref.year
    if (ref - candidate).days > 60:
        return ref.year + 1
    return ref.year


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_time(text: str) -> time | None:
    """Metinden ilk geçerli saati çıkarır ("21:00", "21.00").

    "12.09.2026" gibi sayısal tarihlerin saat sanılmaması için tarihler
    önce maskelenir.
    """
    masked = _NUMERIC_DATE_RE.sub(" ", _norm(text))
    masked = _ISO_RE.sub(lambda m: " " + (m.group(0)[11:] if m.group("hh") else " "), masked)
    for m in _TIME_RE.finditer(masked):
        hh, mm = int(m.group("hh")), int(m.group("mm"))
        if 0 <= hh <= 23:
            return time(hh, mm)
    return None


def parse_dates(raw: str | None, *, ref: date | None = None) -> EventDates | None:
    """Serbest metinden tarih aralığı çıkarır. Bulamazsa ``None``.

    ``ref`` göreli ifadeler ("bugün") ve yıl tahmini için referans gün.
    """
    if not raw:
        return None
    ref = ref or today()
    text = _norm(raw)
    low = text.lower()
    clock = parse_time(text)

    # 1) ISO 8601 (JSON-LD, <time datetime="…">) — en güvenilir kaynak.
    iso = _ISO_RE.search(text)
    if iso:
        d = _safe_date(int(iso["y"]), int(iso["m"]), int(iso["d"]))
        if d:
            t = time(int(iso["hh"]), int(iso["mm"])) if iso["hh"] else clock
            # Aynı metinde ikinci bir ISO tarih varsa bitiş kabul et.
            rest = text[iso.end() :]
            iso2 = _ISO_RE.search(rest)
            end = d
            if iso2:
                d2 = _safe_date(int(iso2["y"]), int(iso2["m"]), int(iso2["d"]))
                if d2 and d2 >= d:
                    end = d2
            return EventDates(d, end, t, is_range=end > d)

    # 2) Aylar arası aralık: "12 Eylül - 3 Ekim 2026"
    m = _RANGE_CROSS_MONTH_RE.search(low)
    if m:
        mon1, mon2 = MONTHS[m["mon1"].lower()], MONTHS[m["mon2"].lower()]
        y2 = _year_for(mon2, int(m["d2"]), ref, int(m["y2"]) if m["y2"] else None)
        y1 = _year_for(mon1, int(m["d1"]), ref, int(m["y1"]) if m["y1"] else None)
        if mon1 > mon2 and not m["y1"]:
            y1 = y2 - 1
        range_start = _safe_date(y1, mon1, int(m["d1"]))
        range_end = _safe_date(y2, mon2, int(m["d2"]))
        if range_start and range_end and range_end >= range_start:
            return EventDates(range_start, range_end, clock, is_range=True)

    # 3) Aynı ay içinde aralık: "12 - 15 Eylül 2026"
    m = _RANGE_SAME_MONTH_RE.search(low)
    if m:
        mon = MONTHS[m["mon"].lower()]
        year = _year_for(mon, int(m["d1"]), ref, int(m["y"]) if m["y"] else None)
        range_start = _safe_date(year, mon, int(m["d1"]))
        range_end = _safe_date(year, mon, int(m["d2"]))
        if range_start and range_end and range_end >= range_start:
            return EventDates(range_start, range_end, clock, is_range=True)

    # 4) Tek tarih: "12 Eylül 2026" / "12 Eyl"
    m = _DMY_RE.search(low)
    if m:
        mon = MONTHS[m["mon"].lower()]
        year = _year_for(mon, int(m["d"]), ref, int(m["y"]) if m["y"] else None)
        d = _safe_date(year, mon, int(m["d"]))
        if d:
            return EventDates(d, d, clock)

    # 5) Sayısal: "12.09.2026"
    m = _NUMERIC_DATE_RE.search(text)
    if m:
        year = int(m["y"])
        year = year + 2000 if year < 100 else year
        d = _safe_date(year, int(m["m"]), int(m["d"]))
        if d:
            return EventDates(d, d, clock)

    # 6) Göreli: "bugün", "yarın"
    for key, offset in _RELATIVE.items():
        if key in low:
            d = ref + timedelta(days=offset)
            return EventDates(d, d, clock)

    # 7) Sadece gün adı: "Cumartesi 21:00" -> bu haftanın/gelecek haftanın o günü
    m = _WEEKDAY_RE.search(low)
    if m:
        target = WEEKDAYS[m["wd"].lower()]
        delta = (target - ref.weekday()) % 7
        d = ref + timedelta(days=delta)
        return EventDates(d, d, clock)

    return None


# --- Kovalama ----------------------------------------------------------------


def week_bounds(ref: date) -> tuple[date, date]:
    """Referans günün içinde bulunduğu haftanın (Pzt-Paz) sınırları."""
    start = ref - timedelta(days=ref.weekday())
    return start, start + timedelta(days=6)


def weekend_bounds(ref: date) -> tuple[date, date]:
    """Bu haftanın Cumartesi-Pazar günleri."""
    week_start, _ = week_bounds(ref)
    return week_start + timedelta(days=5), week_start + timedelta(days=6)


def month_bounds(ref: date) -> tuple[date, date]:
    start = ref.replace(day=1)
    next_month = (start + timedelta(days=32)).replace(day=1)
    return start, next_month - timedelta(days=1)


def bucket_bounds(bucket: Bucket, ref: date | None = None) -> tuple[date, date]:
    """Bir kovanın kapsadığı tarih aralığını döndürür."""
    ref = ref or today()
    _, week_end = week_bounds(ref)
    match bucket:
        case Bucket.TODAY:
            return ref, ref
        case Bucket.TOMORROW:
            d = ref + timedelta(days=1)
            return d, d
        case Bucket.THIS_WEEK:
            return ref, week_end
        case Bucket.WEEKEND:
            sat, sun = weekend_bounds(ref)
            return max(ref, sat), sun
        case Bucket.NEXT_WEEK:
            return week_end + timedelta(days=1), week_end + timedelta(days=7)
        case Bucket.THIS_MONTH:
            _, month_end = month_bounds(ref)
            return ref, month_end
        case Bucket.LATER:
            return week_end + timedelta(days=8), date(ref.year + 5, 12, 31)
        case Bucket.PAST:
            return date(ref.year - 5, 1, 1), ref - timedelta(days=1)
    raise ValueError(bucket)


def classify(dates: EventDates, ref: date | None = None) -> Bucket:
    """Bir etkinliği birincil kovasına yerleştirir.

    Çok günlü etkinlikler "devam ediyor" sayılır: bugün kapsanıyorsa TODAY.
    """
    ref = ref or today()
    if dates.end < ref:
        return Bucket.PAST
    if dates.covers(ref):
        return Bucket.TODAY

    start = max(dates.start, ref)
    _, week_end = week_bounds(ref)
    if start == ref + timedelta(days=1):
        return Bucket.TOMORROW
    if start <= week_end:
        return Bucket.THIS_WEEK
    if start <= week_end + timedelta(days=7):
        return Bucket.NEXT_WEEK
    _, month_end = month_bounds(ref)
    if start <= month_end:
        return Bucket.THIS_MONTH
    return Bucket.LATER


def format_dates(dates: EventDates, *, ref: date | None = None, with_time: bool = True) -> str:
    """İnsan-okur Türkçe tarih metni üretir."""
    ref = ref or today()
    d = dates.start
    same_year = d.year == ref.year

    def one(day: date) -> str:
        base = f"{day.day} {MONTH_NAMES_TR[day.month]}"
        if not same_year or day.year != ref.year:
            base += f" {day.year}"
        return base

    if dates.multi_day:
        if dates.start.month == dates.end.month and dates.start.year == dates.end.year:
            text = f"{dates.start.day}-{dates.end.day} {MONTH_NAMES_TR[dates.end.month]}"
            if not same_year:
                text += f" {dates.end.year}"
        else:
            text = f"{one(dates.start)} – {one(dates.end)}"
        if dates.covers(ref):
            text += " (devam ediyor)"
        return text

    text = f"{one(d)} {WEEKDAY_NAMES_TR[d.weekday()]}"
    if d == ref:
        text = f"Bugün ({text})"
    elif d == ref + timedelta(days=1):
        text = f"Yarın ({text})"
    if with_time and dates.start_time:
        text += f", {dates.start_time.strftime('%H:%M')}"
    return text
