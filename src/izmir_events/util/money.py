"""Fiyat ayrıştırma ve 'ücretsiz mi?' tespiti.

Kaynaklardaki fiyat metinleri:

    "Ücretsiz"  "ÜCRETSİZDİR"  "Giriş Serbest"  "Halka Açık"  "Davetiyelidir"
    "150,00 TL"  "₺150"  "1.250,50 TL"  "150 TL - 400 TL"  "450₺'den başlayan"
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text import clean_whitespace, strip_accents, tr_lower

# Ücretsizliği kesinleştiren ifadeler.
FREE_PATTERNS: tuple[str, ...] = (
    "ucretsiz",
    "ucretsizdir",
    "giris serbest",
    "giris ucretsiz",
    "serbest giris",
    "katilim ucretsiz",
    "herkese acik ve ucretsiz",
    "free",
    "bedava",
    "para alinmaz",
)

# "Ücretsiz" gibi görünse de aslında bilet gerektiren ifadeler.
FREE_BUT_TICKETED: tuple[str, ...] = (
    "davetiye",
    "davetiyelidir",
    "kayit gerekli",
    "rezervasyon zorunlu",
    "kontenjan",
)

# Ücretsiz OLMADIĞINI gösteren ifadeler (öncelikli).
PAID_HINTS: tuple[str, ...] = ("tl", "try", "₺", "usd", "eur", "bilet al", "satin al")

_CURRENCY_RE = re.compile(r"(?:₺|\bTL\b|\bTRY\b)", re.IGNORECASE)
# 1.250,50 | 1250,50 | 1250.50 | 1250
# Türkçe'de binlik ayracı NOKTA'dır, boşluk değil. Boşluğa izin vermek
# "… 2026 750 TL" metnini "026 750" -> 26750 olarak okuyordu.
_NUM = r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_AMOUNT_RE = re.compile(rf"\b(?:{_NUM})\b")
# Para birimine BİTİŞİK sayılar. "18 Eylül ... 650 TL" metninde sadece 650'yi
# yakalar; tarihteki gün numarasını fiyat sanmaz.
_CURRENCY_AMOUNT_RE = re.compile(
    rf"\b(?P<before>{_NUM})\s*(?:₺|\bTL\b|\bTRY\b)"
    rf"|(?:₺|\bTL\b|\bTRY\b)\s*(?P<after>{_NUM})",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Price:
    """Bir etkinliğin fiyat bilgisi.

    ``is_free`` True ise ücretsiz. ``min_amount``/``max_amount`` TL cinsinden.
    Hiçbiri bilinmiyorsa ``unknown`` True olur.
    """

    is_free: bool = False
    min_amount: float | None = None
    max_amount: float | None = None
    currency: str = "TRY"
    raw: str = ""
    needs_registration: bool = False

    @property
    def unknown(self) -> bool:
        return not self.is_free and self.min_amount is None

    def label(self) -> str:
        """Telegram mesajında gösterilecek kısa etiket."""
        if self.is_free:
            return "Ücretsiz (kayıt gerekli)" if self.needs_registration else "Ücretsiz"
        if self.min_amount is None:
            return "Fiyat belirtilmemiş"
        sym = "₺" if self.currency == "TRY" else self.currency
        if self.max_amount and self.max_amount > self.min_amount:
            return f"{_fmt(self.min_amount)}–{_fmt(self.max_amount)} {sym}"
        return f"{_fmt(self.min_amount)} {sym}"

    def merge(self, other: Price) -> Price:
        """İki kaynaktan gelen fiyatı birleştirir (en bilgilendirici kazanır).

        Ücretsizlik, fiyat aralığından daha güçlü bir sinyaldir: bir kaynak
        ücretsiz diyorsa (ve diğeri fiyat vermiyorsa) ücretsiz kabul edilir.
        """
        if self.unknown and not self.is_free:
            return other
        if other.unknown and not other.is_free:
            return self
        if self.is_free and other.is_free:
            return Price(
                is_free=True,
                raw=self.raw or other.raw,
                needs_registration=self.needs_registration or other.needs_registration,
            )
        # Biri ücretsiz biri ücretli: ücretli bilgi daha spesifik, onu koru
        # ama ücretsiz seçenek de olabileceğini fiyatın alt sınırıyla ifade et.
        if self.is_free != other.is_free:
            paid = other if other.min_amount is not None else self
            return paid
        # İki kaynağın bildirdiği tüm tutarlar tek bir aralığa toplanır:
        # biri "150 TL" diğeri "400 TL" diyorsa gerçek aralık 150–400'dür.
        values = [
            amount
            for amount in (self.min_amount, self.max_amount, other.min_amount, other.max_amount)
            if amount is not None
        ]
        if not values:
            return Price(is_free=False, currency=self.currency, raw=self.raw or other.raw)
        low, high = min(values), max(values)
        return Price(
            is_free=False,
            min_amount=low,
            max_amount=high if high > low else None,
            currency=self.currency or other.currency,
            raw=self.raw or other.raw,
        )


def _fmt(amount: float) -> str:
    """Türkçe sayı biçimi: 1.250 / 1.250,50."""
    if amount == int(amount):
        return f"{int(amount):,}".replace(",", ".")
    return f"{amount:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")


def _to_float(token: str) -> float | None:
    """ "1.250,50" -> 1250.5 ; "150.00" -> 150.0 ; "1 250" -> 1250."""
    token = token.replace(" ", "")
    if "," in token:  # Türkçe: nokta binlik, virgül ondalık
        token = token.replace(".", "").replace(",", ".")
    elif token.count(".") == 1:
        whole, frac = token.split(".")
        # "1.250" binlik ayracı, "150.00" ondalık
        if len(frac) == 3 and len(whole) <= 3:
            token = whole + frac
    else:
        token = token.replace(".", "")
    try:
        return float(token)
    except ValueError:
        return None


def parse_price(raw: str | None, *, free_by_default: bool = False) -> Price:
    """Serbest metinden fiyat bilgisi çıkarır.

    ``free_by_default``: belediye/müze gibi kaynaklarda fiyat hiç
    belirtilmemişse etkinlik ücretsiz kabul edilir.
    """
    if not raw or not raw.strip():
        return Price(is_free=free_by_default, raw="")

    text = clean_whitespace(raw)
    flat = strip_accents(tr_lower(text))

    has_amount = bool(_CURRENCY_RE.search(text)) and bool(_AMOUNT_RE.search(text))

    if any(p in flat for p in FREE_PATTERNS) and not has_amount:
        return Price(
            is_free=True,
            raw=text,
            needs_registration=any(p in flat for p in FREE_BUT_TICKETED),
        )

    amounts: list[float] = []

    def _add(token: str | None) -> None:
        if not token:
            return
        value = _to_float(token)
        # Yıl (2026) gibi sayıları fiyat sanma.
        if value is None or value <= 0 or 1900 <= value <= 2100:
            return
        amounts.append(value)

    # Öncelik: para birimine bitişik sayılar (en güvenilir sinyal).
    for m in _CURRENCY_AMOUNT_RE.finditer(text):
        _add(m.group("before") or m.group("after"))

    # Para birimi yok ama metin açıkça fiyat alanıysa ("Fiyat: 150") serbest sayı ara.
    # Uzun metinlerde (kart içeriği) bu yola gitme: tarih/saat rakamları yanıltır.
    if not amounts and len(text) <= 60 and any(h in flat for h in ("fiyat", "ucret", "bilet")):
        for m in _AMOUNT_RE.finditer(text):
            _add(m.group(0))

    if amounts:
        return Price(
            is_free=False,
            min_amount=min(amounts),
            max_amount=max(amounts) if len(amounts) > 1 else None,
            currency="TRY",
            raw=text,
        )

    if any(p in flat for p in FREE_PATTERNS):
        return Price(is_free=True, raw=text)

    if any(h in flat for h in PAID_HINTS):
        return Price(is_free=False, raw=text)

    return Price(is_free=free_by_default, raw=text)
