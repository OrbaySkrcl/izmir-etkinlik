"""İki etkinliğin "aynı etkinlik" olup olmadığını puanlar.

Zorluk şu: kaynaklar aynı etkinliği farklı yazar.

    Bubilet     : "Sezen Aksu"
    Biletinial  : "Sezen Aksu Konseri"
    OGGUSTO     : "Sezen Aksu - Kültürpark Açıkhava Tiyatrosu"
    İzmirMag    : "Efsane Sanatçı Sezen Aksu İzmir'de!"

Ama şunlar AYRI etkinlik:

    "Hamlet"  ≠  "Hamlet Makinesi"
    "Sezen Aksu 12 Eylül"  ≠  "Sezen Aksu 13 Eylül"   (farklı gün)
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from rapidfuzz import fuzz

from ..models import Event

# Puanlama ağırlıkları. Başlık baskın sinyal; mekan doğrulayıcı.
W_TITLE = 0.72
W_VENUE = 0.28

# Bu eşiğin altındaki başlık benzerliğinde mekan ne olursa olsun eşleşme yok.
TITLE_FLOOR = 0.55
# Bu eşiğin üstünde başlıklar pratikte aynı sayılır.
TITLE_CEILING = 0.97
# Mekan benzerliği bunun altındaysa "farklı mekan" kabul edilir.
VENUE_CONFLICT = 0.4


@dataclass(frozen=True, slots=True)
class Match:
    """Karşılaştırma sonucu ve gerekçesi (hata ayıklama için)."""

    score: float
    reason: str

    def __bool__(self) -> bool:  # pragma: no cover - kolaylık
        return self.score > 0


def _canonical_url(url: str | None) -> str | None:
    """Sorgu parametrelerinden arındırılmış URL anahtarı."""
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.netloc:
        return None
    path = parsed.path.rstrip("/").lower()
    if not path or path == "/":
        return None
    return f"{parsed.netloc.lower().removeprefix('www.')}{path}"


def title_similarity(a: str, b: str) -> float:
    """0-1 aralığında başlık benzerliği.

    ``token_set_ratio`` alt küme ilişkisinde 100 döner ("hamlet" ⊂
    "hamlet makinesi"), bu da yanlış eşleşme üretir. ``token_sort_ratio``
    ise uzunluk farkını cezalandırır. İkisinin ortalaması, gürültü
    sözcüğü eklenmiş başlıkları eşleştirirken farklı yapıtları ayırır.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    token_set = fuzz.token_set_ratio(a, b) / 100.0
    token_sort = fuzz.token_sort_ratio(a, b) / 100.0
    return (token_set + token_sort) / 2.0


def venue_similarity(a: str, b: str) -> float | None:
    """Mekan benzerliği; taraflardan biri boşsa ``None`` (bilgi yok)."""
    if not a or not b:
        return None
    if a == b:
        return 1.0
    return fuzz.token_set_ratio(a, b) / 100.0


def compare(a: Event, b: Event, *, date_tolerance_days: int = 1) -> Match:
    """İki etkinliği karşılaştırır ve 0-1 arası eşleşme puanı döndürür."""
    # 1) Tarih kapısı: aralıklar kesişmiyorsa aynı etkinlik olamaz.
    if not a.dates.overlaps(b.dates, tolerance_days=date_tolerance_days):
        return Match(0.0, "tarihler kesişmiyor")

    # 2) Aynı kanonik URL: kesin eşleşme (aynı etkinliğin aynı sayfası).
    url_a, url_b = _canonical_url(a.primary_url()), _canonical_url(b.primary_url())
    if url_a and url_a == url_b:
        return Match(1.0, "aynı URL")

    # 3) Tek kelimelik başlıklar fazla genel: "Hamlet" ile "Hamlet Makinesi"
    #    arasındaki tek token'lık fark yapıtın kimliğini değiştirir. Bu yüzden
    #    taraflardan biri tek kelimeyse birebir eşitlik aranır.
    tokens_a, tokens_b = set(a.norm_title.split()), set(b.norm_title.split())
    if min(len(tokens_a), len(tokens_b)) <= 1 and tokens_a != tokens_b:
        return Match(0.0, "tek kelimelik başlık, birebir eşleşme yok")

    t_score = title_similarity(a.norm_title, b.norm_title)
    if t_score < TITLE_FLOOR:
        return Match(0.0, f"başlık uzak ({t_score:.2f})")

    v_score = venue_similarity(a.norm_venue, b.norm_venue)

    # 4) Mekanlar biliniyor ve açıkça farklıysa, başlık birebir aynı olsa bile
    #    ayrı etkinlik olabilir: aynı oyun aynı gece iki farklı sahnede
    #    oynanabiliyor. Bu yüzden mekan farkı başlık eşitliğini geçersiz kılar.
    if v_score is not None and v_score < VENUE_CONFLICT:
        score = (W_TITLE * t_score + W_VENUE * v_score) * 0.75
        return Match(score, f"mekanlar farklı (başlık {t_score:.2f}, mekan {v_score:.2f})")

    # 5) Başlıklar pratikte aynı ve mekan çelişmiyor.
    if t_score >= TITLE_CEILING:
        return Match(1.0, f"başlık aynı ({t_score:.2f})")

    if v_score is None:
        # Mekan bilgisi yok: sadece başlığa güven, ama tek sinyale
        # dayandığımız için hafif bir güven indirimi uygula.
        return Match(t_score * 0.97, f"başlık {t_score:.2f}, mekan bilinmiyor")

    return Match(
        W_TITLE * t_score + W_VENUE * v_score, f"başlık {t_score:.2f}, mekan {v_score:.2f}"
    )
