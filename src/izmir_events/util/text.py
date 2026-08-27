"""Türkçe'ye duyarlı metin normalizasyonu.

Tekilleştirmenin (dedup) kalitesi büyük ölçüde buradaki normalizasyona bağlı.
Farklı kaynaklar aynı etkinliği şu şekillerde yazabiliyor:

    "Sezen Aksu Konseri"
    "SEZEN AKSU | İzmir"
    "Sezen Aksu - Kültürpark Açıkhava Tiyatrosu"
    "Sezen Aksu (İzmir) Bileti"

Hepsi ``sezen aksu`` çekirdeğine indirgenmeli.
"""

from __future__ import annotations

import re
import unicodedata

# --- Türkçe büyük/küçük harf dönüşümü ---------------------------------------
# str.lower() Türkçe'de hatalı: "I".lower() -> "i" (olması gereken "ı"),
# "İ".lower() -> "i̇" (birleşik nokta kalıyor).
_LOWER_MAP = str.maketrans(
    {
        "I": "ı",
        "İ": "i",
        "Ş": "ş",
        "Ğ": "ğ",
        "Ü": "ü",
        "Ö": "ö",
        "Ç": "ç",
    }
)
_UPPER_MAP = str.maketrans({"ı": "I", "i": "İ", "ş": "Ş", "ğ": "Ğ", "ü": "Ü", "ö": "Ö", "ç": "Ç"})

# Türkçe karakterlerin ASCII karşılıkları (aksan farkını yok saymak için).
_ASCII_MAP = str.maketrans(
    {
        "ı": "i",
        "ş": "s",
        "ğ": "g",
        "ü": "u",
        "ö": "o",
        "ç": "c",
        "â": "a",
        "î": "i",
        "û": "u",
        "ä": "a",
        "é": "e",
    }
)


def tr_lower(text: str) -> str:
    """Türkçe kurallarına uyan küçük harfe çevirme."""
    return text.translate(_LOWER_MAP).lower()


def tr_upper(text: str) -> str:
    """Türkçe kurallarına uyan büyük harfe çevirme."""
    return text.translate(_UPPER_MAP).upper()


def tr_title(text: str) -> str:
    """Türkçe'ye uygun başlık formatı ("izmir" -> "İzmir")."""
    return " ".join(tr_upper(w[:1]) + tr_lower(w[1:]) if w else w for w in text.split(" "))


def strip_accents(text: str) -> str:
    """Türkçe karakterleri ASCII'ye indirger ("şarkı" -> "sarki")."""
    text = text.translate(_ASCII_MAP)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_WS_RE = re.compile(r"\s+")
_ZERO_WIDTH_RE = re.compile(r"[​-‏‪-‮﻿]")


def clean_whitespace(text: str) -> str:
    """Sıfır genişlikli karakterleri atar, boşlukları teke indirir."""
    if not text:
        return ""
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


# --- Başlık normalizasyonu ---------------------------------------------------

# Etkinliğin kimliğini değiştirmeyen, kaynaktan kaynağa değişen gürültü sözcükleri.
NOISE_TOKENS: frozenset[str] = frozenset(
    {
        "bileti",
        "biletleri",
        "bilet",
        "etkinligi",
        "etkinlik",
        "etkinlikleri",
        "konseri",
        "konser",
        "gosterisi",
        "gosteri",
        "oyunu",
        "tiyatro",  # "… Tiyatro Oyunu" kuyruğu
        "izmir",
        "izmirde",
        "de",
        "da",
        "sahnesi",
        "turnesi",
        "turne",
        "resitali",
        "resital",
        "canli",
        "performans",
        "performansi",
        "sunar",
        "ile",
        "and",
        "the",
        "live",
        "in",
        "at",
        "tickets",
        "ticket",
        "event",
        "yeni",
        "ozel",
        "gala",
        "seansi",
        "seans",
        # Haber/magazin başlıklarındaki tanıtım dolgusu
        "efsane",
        "unlu",
        "sanatci",
        "sanatcisi",
        "muhtesem",
        "sahnede",
        "sahne",
        "geliyor",
        "bulusuyor",
        "hayranlariyla",
        "sevilen",
        "usta",
    }
)

# Başlık sonundaki/başındaki temizlenecek kalıplar.
_BRACKET_RE = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_DIGIT_ONLY_RE = re.compile(r"^\d+$")

# "Sanatçı - Mekan" / "Sanatçı | İzmir" ayraçları.
_SEPARATOR_RE = re.compile(r"\s+[-–—|•·:]\s+")

_LEADING_JUNK_RE = re.compile(
    r"^(?:izmir|izmir'?de|bugun|yarin|bu\s+hafta)\s+", flags=re.IGNORECASE
)


def _is_meaningful(part: str) -> bool:
    """Parça, gürültü sözcüğü olmayan en az bir token içeriyor mu?

    ``"İzmir"`` anlamsızdır (her başlıkta geçebilir), ``"Sezen Aksu"`` anlamlıdır.
    """
    flat = strip_accents(tr_lower(_PUNCT_RE.sub(" ", part)))
    return any(t not in NOISE_TOKENS and len(t) > 1 for t in flat.split())


def core_title(title: str) -> str:
    """Başlığın 'çekirdeğini' döndürür: ayraçla ayrılmış ilk anlamlı parça.

    ``"Sezen Aksu - Kültürpark Açıkhava"`` -> ``"Sezen Aksu"``
    ``"İzmir | Sezen Aksu"``               -> ``"Sezen Aksu"``

    Hiçbir parça anlamlı değilse (ör. ``"Konser | Bilet"``) başlık bütün
    olarak döndürülür; boş anahtar üretmek tekilleştirmeyi bozar.
    """
    title = clean_whitespace(title)
    if not title:
        return ""
    parts = [p.strip() for p in _SEPARATOR_RE.split(title) if p.strip()]
    if len(parts) < 2:
        return title
    for part in parts:
        if len(part) >= 3 and _is_meaningful(part):
            return part
    return title


def normalize_title(title: str, *, keep_core: bool = True) -> str:
    """Karşılaştırma için başlığı kanonik forma indirger.

    Sonuç: aksansız, küçük harfli, noktalama ve gürültü sözcüklerinden
    arındırılmış, alfabetik olmayan tek karakterleri atılmış token dizisi.
    """
    if not title:
        return ""
    text = clean_whitespace(title)
    if keep_core:
        text = core_title(text)
    text = _BRACKET_RE.sub(" ", text)
    text = tr_lower(text)
    text = _LEADING_JUNK_RE.sub("", text)
    text = strip_accents(text)
    text = _YEAR_RE.sub(" ", text)
    text = text.replace("&", " ve ")
    text = _PUNCT_RE.sub(" ", text)
    tokens = [t for t in text.split() if t]
    kept = [
        t for t in tokens if t not in NOISE_TOKENS and len(t) > 1 and not _DIGIT_ONLY_RE.match(t)
    ]
    # Her şey gürültüyse orijinal token'lara geri dön (boş anahtar üretme).
    if not kept:
        kept = [t for t in tokens if len(t) > 1] or tokens
    return " ".join(kept)


def title_tokens(title: str) -> frozenset[str]:
    """Normalize edilmiş başlığın token kümesi."""
    return frozenset(normalize_title(title).split())


# --- Mekan normalizasyonu ----------------------------------------------------

# Mekan adlarında sık geçen ve ayırt ediciliği düşük kuyruklar.
_VENUE_NOISE = frozenset(
    {
        "sahnesi",
        "sahne",
        "salonu",
        "salon",
        "merkezi",
        "merkez",
        "kultur",
        "sanat",
        "kongre",
        "acikhava",
        "acik",
        "hava",
        "tiyatrosu",
        "tiyatro",
        "izmir",
        "amfi",
        "amfitiyatro",
        "arena",
        "muzesi",
        "muze",
        "gosteri",
        "etkinlik",
        "alani",
        "avm",
        "otel",
        "hotel",
    }
)

# Sık kullanılan mekan takma adları -> kanonik anahtar.
VENUE_ALIASES: dict[str, str] = {
    "kulturpark acikhava tiyatrosu": "kulturpark acikhava",
    "kulturpark acikhava": "kulturpark acikhava",
    "kulturpark": "kulturpark acikhava",
    "ahmed adnan saygun sanat merkezi": "aassm",
    "aassm": "aassm",
    "aasm": "aassm",
    "izmir ahmed adnan saygun": "aassm",
    "ataturk kultur merkezi": "akm",
    "akm": "akm",
    "izmir sanat": "izmir sanat",
    "fuar izmir": "fuar izmir",
    "izmir arena": "izmir arena",
    "hangout performance hall": "hangout",
    "kedi kultur sanat merkezi": "kedi ksm",
    "kedi ksm": "kedi ksm",
}


def normalize_venue(venue: str | None) -> str:
    """Mekan adını karşılaştırılabilir anahtara çevirir."""
    if not venue:
        return ""
    text = strip_accents(tr_lower(clean_whitespace(venue)))
    text = _BRACKET_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if text in VENUE_ALIASES:
        return VENUE_ALIASES[text]
    for alias, canonical in VENUE_ALIASES.items():
        if alias in text:
            return canonical
    tokens = [t for t in text.split() if t not in _VENUE_NOISE and len(t) > 1]
    return " ".join(tokens) if tokens else text


def slugify(text: str, *, max_length: int = 80) -> str:
    """URL/anahtar dostu slug üretir."""
    base = strip_accents(tr_lower(clean_whitespace(text)))
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base[:max_length].strip("-")


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    """Kelime sınırına saygı göstererek kısaltır."""
    text = clean_whitespace(text)
    if len(text) <= limit:
        return text
    cut = text[: max(0, limit - len(suffix))]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:-") + suffix


# --- Gösterim için başlık düzeltme -------------------------------------------

_TRAILING_CITY_RE = re.compile(
    r"\s*[-–—|•·]\s*(?:izmir|İzmir|IZMIR|İZMİR|izmir'?de)\s*$", re.IGNORECASE
)


def is_shouting(text: str) -> bool:
    """Başlık büyük harfle mi yazılmış? (En az 3 harf ve harflerin %80'i büyük.)"""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 3:
        return False
    upper = sum(1 for c in letters if c == tr_upper(c) and c != tr_lower(c))
    return upper / len(letters) >= 0.8


def clean_display_title(title: str) -> str:
    """Kullanıcıya gösterilecek başlığı düzeltir.

    ``"SEZEN AKSU | İZMİR"`` -> ``"Sezen Aksu"``
    Anlamı değiştirmez; sadece kaynaktan gelen biçim gürültüsünü alır.
    """
    text = clean_whitespace(title)
    if not text:
        return text
    text = _TRAILING_CITY_RE.sub("", text).strip(" -–—|•·")
    if is_shouting(text):
        text = tr_title(tr_lower(text))
    return clean_whitespace(text) or clean_whitespace(title)


# --- Başlıktan mekan ayırma --------------------------------------------------

# Bir metin parçasının mekan adı olduğunu gösteren sözcükler.
VENUE_HINT_RE = re.compile(
    r"\b("
    r"tiyatro(su)?|sahne(si)?|salon(u)?|oditoryum|amfi(tiyatro)?|arena|stadyum|"
    r"a[çc][ıi]khava|k[üu]lt[üu]rpark|kongre|fuar|m[üu]ze(si)?|galeri(si)?|"
    r"sanat\s+merkezi|k[üu]lt[üu]r\s+merkezi|performance\s+hall|"
    r"avm|otel|hotel|kamp[üu]s|[üu]niversite|konservatuvar|kitapevi|kitab[ei]vi"
    r")\b",
    re.IGNORECASE,
)

# Başlıkta mekanı ayıran işaretler: " / ", " | ", " - ".
_TITLE_VENUE_SPLIT_RE = re.compile(r"\s*[/|]\s*|\s+[-–—]\s+")


def split_venue_from_title(title: str) -> tuple[str, str | None]:
    """Başlığın sonuna yapıştırılmış mekan adını ayırır.

    ``"Konken Partisi / Bostanlı Suat Taşer Tiyatrosu"``
    -> ``("Konken Partisi", "Bostanlı Suat Taşer Tiyatrosu")``

    Yalnızca ayraçtan *sonraki* parça mekan sözcüğü içeriyorsa bölünür;
    ``"Tiyatro Oyunu - Hamlet"`` gibi başlıklar olduğu gibi kalır. Ayrıca
    geriye anlamlı bir başlık kalmıyorsa bölme yapılmaz.
    """
    title = clean_whitespace(title)
    if not title:
        return title, None
    parts = [p.strip() for p in _TITLE_VENUE_SPLIT_RE.split(title) if p.strip()]
    if len(parts) < 2:
        return title, None

    # Sondan başa doğru ilk mekan adayını bul.
    for index in range(len(parts) - 1, 0, -1):
        candidate = parts[index]
        if not VENUE_HINT_RE.search(candidate):
            continue
        remaining = " / ".join(parts[:index]).strip()
        # Geriye anlamlı bir başlık kalmalı, yoksa mekan zaten başlığın kendisi.
        if len(remaining) < 4 or not _is_meaningful(remaining):
            return title, None
        return remaining, candidate
    return title, None


# --- Başlık kuyruğu temizliği ------------------------------------------------

# Listeleme sayfalarının başlığa eklediği, etkinlikle ilgisi olmayan etiketler.
_TRAILING_NOISE_WORDS = frozenset({"guncel", "populer", "onerilen", "vitrin"})

# "İstanbul Avrupa" / "İzmir Anadolu" gibi bölge etiketleri.
_REGION_WORDS = frozenset({"avrupa", "anadolu"})
_REGION_CITIES = frozenset({"istanbul", "izmir", "ankara", "bursa", "antalya"})

_TAIL_PUNCT = " -–—|/,·•;:"


def _flat(text: str) -> str:
    return strip_accents(tr_lower(text)).strip(_TAIL_PUNCT + ".")


def strip_trailing_venue(title: str, venue: str | None) -> str:
    """Başlığın sonunda mekan adı tekrarlanıyorsa atar.

    ``("Adamlar / Harbiye Açıkhava Sahnesi", "Harbiye Açıkhava Sahnesi")``
    -> ``"Adamlar"``

    Karşılaştırma token bazlı yapılır: aksan sadeleştirme karakter sayısını
    değiştirebildiği için dizin aritmetiği güvenli değil.
    """
    if not venue:
        return title
    title_tokens = title.split()
    venue_tokens = venue.split()
    if not venue_tokens or len(title_tokens) <= len(venue_tokens):
        return title
    tail = [_flat(t) for t in title_tokens[-len(venue_tokens) :]]
    if tail != [_flat(t) for t in venue_tokens]:
        return title
    remaining = " ".join(title_tokens[: -len(venue_tokens)]).strip(_TAIL_PUNCT)
    return remaining if len(remaining) >= 3 else title


def strip_trailing_tags(title: str, *, drop_city: bool = False) -> str:
    """Sondaki listeleme etiketlerini atar.

    ``"Sezen Aksu İzmir Avrupa GÜNCEL"`` -> ``"Sezen Aksu"``

    ``drop_city``: sondaki tek başına şehir adını da atar. Yalnızca başlıktan
    zaten bir mekan sökülmüşse açılmalı; o zaman şehir adı bir konum etiketidir.
    Aksi halde ``"Elveda İstanbul"`` gibi gerçek başlıklar bozulur.
    """
    tokens = title.split()
    while tokens:
        last = _flat(tokens[-1])
        if last in _TRAILING_NOISE_WORDS:
            tokens.pop()
            continue
        if len(tokens) >= 2 and last in _REGION_WORDS and _flat(tokens[-2]) in _REGION_CITIES:
            del tokens[-2:]
            continue
        if drop_city and len(tokens) >= 2 and last in _REGION_CITIES:
            tokens.pop()
            continue
        break
    cleaned = " ".join(tokens).strip(_TAIL_PUNCT)
    return cleaned if len(cleaned) >= 3 else title
