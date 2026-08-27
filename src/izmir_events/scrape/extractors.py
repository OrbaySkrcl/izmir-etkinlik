"""HTML'den etkinlik çıkarma stratejileri.

Sabit CSS seçicilere güvenmek kırılgandır: siteler tasarım değiştirdiğinde
scraper sessizce boş döner. Bu yüzden dört strateji sırayla denenir ve
ilk anlamlı sonuç veren kullanılır:

1. ``jsonld``    – schema.org/Event. Bilet sitelerinin çoğu SEO için yayınlar;
                   makine-okur olduğu için en güvenilir kaynak.
2. ``nextdata``  – Next.js siteleri sayfa verisini ``__NEXT_DATA__`` içinde
                   JSON olarak gömer; HTML değişse de bu yapı kalır.
3. ``selectors`` – ``sources.yaml``'daki CSS seçicileri. Kod değiştirmeden
                   yapılandırmayla düzeltilebilir.
4. ``heuristic`` – Tekrar eden "kart" yapısını kendi başına bulur. Son çare,
                   ama site tamamen yenilendiğinde bile bir şeyler döndürür.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

import structlog
from selectolax.parser import HTMLParser, Node

from ..models import RawEvent
from ..util.dates import is_date_only, parse_dates, strip_date_expressions
from ..util.text import VENUE_HINT_RE, clean_whitespace
from .http import absolutize

log = structlog.get_logger(__name__)

# schema.org'da Event'ten türeyen tipler.
EVENT_TYPES = {
    "event",
    "musicevent",
    "theaterevent",
    "festival",
    "exhibitionevent",
    "screeningevent",
    "comedyevent",
    "danceevent",
    "educationevent",
    "sportsevent",
    "socialevent",
    "literaryevent",
    "childrensevent",
    "businessevent",
    "foodevent",
    "visualartsevent",
    "publicationevent",
}


# --- seçici yardımcıları -----------------------------------------------------


def parse_selector(spec: str) -> list[tuple[str, str | None]]:
    """``"a@href, .title"`` -> ``[("a", "href"), (".title", None)]``.

    Virgülle ayrılmış adaylar sırayla denenir; ``@attr`` niteliği okur.
    """
    out: list[tuple[str, str | None]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "@" in part:
            css, _, attr = part.rpartition("@")
            out.append((css.strip() or "*", attr.strip()))
        else:
            out.append((part, None))
    return out


# Basit CSS seçicileri: "div", ".card", "#main", "a.link", "[href]", "a[href*='/e/']"
_SIMPLE_SELECTOR_RE = re.compile(
    r"^(?P<tag>[a-zA-Z][\w-]*)?"
    r"(?P<classes>(?:\.[\w-]+)*)"
    r"(?P<id>#[\w-]+)?"
    r"(?P<attr>\[[^\]]+\])?$"
)
_ATTR_RE = re.compile(
    r"^\[\s*(?P<name>[\w-]+)\s*(?:(?P<op>[*^$~|]?=)\s*['\"]?(?P<value>[^'\"\]]*)['\"]?)?\s*\]$"
)


def self_matches(node: Node, css: str) -> bool:
    """Düğümün *kendisi* seçiciyle eşleşiyor mu?

    selectolax'ın ``css_matches`` metodu alt ağaçta eşleşme arar, düğümün
    kendisini sınamaz; bu yüzden kart elemanının kendisi ``<a>`` olduğunda
    ``"a@href"`` gibi seçiciler boş dönerdi. Burada yaygın basit seçici
    biçimleri elle sınanır; karmaşık seçicilerde ``False`` döndürülür
    (yanlış eşleşmektense kaçırmak yeğdir).
    """
    match = _SIMPLE_SELECTOR_RE.match(css.strip())
    if not match:
        return False

    tag = match.group("tag")
    if tag and tag != "*" and node.tag != tag.lower():
        return False

    classes = match.group("classes")
    if classes:
        node_classes = set((node.attributes.get("class") or "").split())
        if not {c for c in classes.split(".") if c} <= node_classes:
            return False

    node_id = match.group("id")
    if node_id and node.attributes.get("id") != node_id[1:]:
        return False

    attr = match.group("attr")
    if attr:
        attr_match = _ATTR_RE.match(attr)
        if not attr_match:
            return False
        value = node.attributes.get(attr_match.group("name"))
        if value is None:
            return False
        wanted, op = attr_match.group("value"), attr_match.group("op")
        if not op or wanted is None or wanted == "":
            return True
        match op:
            case "=":
                return value == wanted
            case "*=":
                return wanted in value
            case "^=":
                return value.startswith(wanted)
            case "$=":
                return value.endswith(wanted)
            case _:
                return wanted in value

    # Hiçbir bileşen yoksa (boş seçici) eşleşme sayma.
    return bool(tag or classes or node_id or attr)


def select_value(node: Node, spec: str, *, self_ok: bool = True) -> str | None:
    """Bir düğüm içinde seçiciyi uygular, ilk dolu değeri döndürür."""
    if not spec:
        return None
    for css, attr in parse_selector(spec):
        candidates: list[Node] = []
        # Seçici düğümün kendisiyle eşleşiyorsa onu da değerlendir.
        # (Kart elemanının kendisi <a> olabilir; o zaman "a@href" içeride değil,
        # düğümün kendisinde aranmalı.)
        if self_ok and self_matches(node, css):
            candidates.append(node)
        try:
            candidates.extend(node.css(css))
        except ValueError:  # geçersiz CSS seçici
            continue
        for found in candidates:
            value = found.attributes.get(attr) if attr else found.text(separator=" ")
            value = clean_whitespace(value or "")
            if value:
                return value
    return None


def node_text(node: Node) -> str:
    return clean_whitespace(node.text(separator=" "))


# Kart metninde fiyat bilgisi olduğunu gösteren işaretler.
_PRICE_HINT_RE = re.compile(
    r"(₺|\bTL\b|\bTRY\b|[üu]cretsiz|giri[şs]\s+serbest|bedava)", re.IGNORECASE
)

# Başlık adayı aranacak elemanlar, belirginlik sırasına göre.
_TITLE_CANDIDATE_CSS = ("h1", "h2", "h3", "h4", "h5", ".title", "[class*='title']", ".name")


def pick_title(node: Node, spec: str = "") -> str | None:
    """Karttan en makul başlığı seçer.

    Adaylar sırayla denenir ve *tarihten ibaret* olanlar atlanır: bazı
    sayfalarda başlık seçicisi tutmadığında tarih elemanı başlık sanılıp
    ``"29 Ağustos 2026"`` gibi sahte etkinlikler üretiliyordu.
    """
    candidates: list[str] = []
    if spec:
        value = select_value(node, spec)
        if value:
            candidates.append(value)
    for css in _TITLE_CANDIDATE_CSS:
        found = node.css_first(css)
        if found:
            candidates.append(node_text(found))
    link = node.css_first("a")
    if link:
        candidates.append(node_text(link))
        candidates.append(clean_whitespace(link.attributes.get("title") or ""))
    image = node.css_first("img[alt]")
    if image:
        candidates.append(clean_whitespace(image.attributes.get("alt") or ""))

    for candidate in candidates:
        if candidate and len(candidate) >= 3 and not is_date_only(candidate):
            return candidate
    return None


def price_from_card(node: Node, existing: str | None) -> str | None:
    """Fiyat seçicisi tutmadıysa kart metninde fiyat izi arar.

    Fiyat ayrıştırıcı tutarları yalnızca para birimine bitişikse okuduğu
    için kart metnini vermek güvenli: tarihteki gün numarası fiyat sanılmaz.
    """
    if existing:
        return existing
    text = node_text(node)
    if not _PRICE_HINT_RE.search(text):
        return None
    # Tarihleri at: yıl ve gün rakamları tutar ayrıştırmasını yanıltmasın.
    return strip_date_expressions(text) or text


# Kart metnini mantıklı parçalara bölen ayraçlar.
_CHUNK_SPLIT_RE = re.compile(r"\s*[·•|/]\s*|\s*\n\s*|\s{2,}|\s+[-–—]\s+")


def venue_from_card(node: Node, existing: str | None, title: str | None = None) -> str | None:
    """Mekan seçicisi tutmadıysa kart metninde mekan adı arar.

    Kartın alt elemanları tek tek gezilir (düz metinde satır yapısı
    kaybolduğu için), her elemanın metni ayraçlara göre parçalanır ve
    mekan sözcüğü ("Sahnesi", "Tiyatrosu", "Arena"…) içeren kısa bir parça
    aranır. Tarih veya fiyat içeren parçalar elenir; böylece
    ``"Konak Sahnesi · 400 TL"`` metninden yalnızca ``"Konak Sahnesi"`` alınır.
    """
    if existing:
        return existing
    normalized_title = clean_whitespace(title or "")

    elements = node.css("span, div, p, small, li, strong, em, address, h5, h6")
    # Küçük elemanlar önce: tüm kartı kapsayan kapsayıcılar yanıltmasın.
    for element in sorted(elements, key=lambda n: len(node_text(n))):
        text = node_text(element)
        if not text or len(text) > 160:
            continue
        for chunk in _CHUNK_SPLIT_RE.split(text):
            chunk = clean_whitespace(chunk).strip(",;:·-–—")
            if not (4 <= len(chunk) <= 60) or chunk == normalized_title:
                continue
            if not VENUE_HINT_RE.search(chunk):
                continue
            if _PRICE_HINT_RE.search(chunk) or parse_dates(chunk) is not None:
                continue
            return chunk
    return None


# --- 1) JSON-LD --------------------------------------------------------------


def _walk_json(obj: Any) -> Iterable[dict[str, Any]]:
    """JSON ağacındaki tüm sözlükleri dolaşır (@graph, ItemList vb. dahil)."""
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk_json(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_json(item)


def _is_event_node(obj: dict[str, Any]) -> bool:
    raw_type = obj.get("@type") or obj.get("type")
    types = raw_type if isinstance(raw_type, list) else [raw_type]
    return any(isinstance(t, str) and t.lower() in EVENT_TYPES for t in types)


def _jsonld_location(obj: Any) -> str | None:
    """schema.org Place/PostalAddress yapısından okunabilir mekan adı."""
    if isinstance(obj, str):
        return clean_whitespace(obj)
    if isinstance(obj, list):
        for item in obj:
            name = _jsonld_location(item)
            if name:
                return name
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    if isinstance(name, str) and name.strip():
        return clean_whitespace(name)
    address = obj.get("address")
    if isinstance(address, dict):
        parts = [address.get("name"), address.get("streetAddress"), address.get("addressLocality")]
        joined = " ".join(clean_whitespace(str(p)) for p in parts if p)
        return joined or None
    if isinstance(address, str):
        return clean_whitespace(address)
    return None


def _jsonld_price(obj: dict[str, Any]) -> str | None:
    """offers alanından fiyat metni üretir."""
    offers = obj.get("offers")
    if not offers:
        return None
    if isinstance(offers, dict):
        offers = [offers]
    if not isinstance(offers, list):
        return None
    prices: list[float] = []
    currency = "TL"
    free_flag = False
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        currency = offer.get("priceCurrency") or currency
        for key in ("price", "lowPrice", "highPrice"):
            value = offer.get(key)
            if value in (None, ""):
                continue
            try:
                amount = float(str(value).replace(",", "."))
            except ValueError:
                continue
            if amount == 0:
                free_flag = True
            else:
                prices.append(amount)
    if prices:
        symbol = "TL" if currency in ("TRY", "TL") else currency
        return f"{min(prices)} {symbol}" + (f" - {max(prices)} {symbol}" if len(prices) > 1 else "")
    return "Ücretsiz" if free_flag else None


def _first_str(value: Any) -> str | None:
    """schema.org / JSON alanları string, sayı, liste veya nesne olabilir."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return clean_whitespace(value) or None
    if isinstance(value, list):
        for item in value:
            got = _first_str(item)
            if got:
                return got
    if isinstance(value, dict):
        return _first_str(value.get("name") or value.get("url"))
    return None


def extract_jsonld(html: str, base_url: str, source_key: str) -> list[RawEvent]:
    """``application/ld+json`` bloklarından Event kayıtlarını çıkarır."""
    tree = HTMLParser(html)
    events: list[RawEvent] = []
    seen: set[str] = set()

    for script in tree.css('script[type="application/ld+json"]'):
        payload = script.text()
        if not payload or "vent" not in payload:  # "Event"/"event" geçmiyorsa atla
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            # Bazı siteler birden fazla JSON nesnesini yan yana koyar.
            fixed = re.sub(r"}\s*{", "},{", payload.strip())
            try:
                data = json.loads(f"[{fixed}]")
            except json.JSONDecodeError:
                continue

        for obj in _walk_json(data):
            if not _is_event_node(obj):
                continue
            title = _first_str(obj.get("name"))
            if not title:
                continue
            url = absolutize(base_url, _first_str(obj.get("url")))
            key = f"{title}|{obj.get('startDate')}"
            if key in seen:
                continue
            seen.add(key)
            events.append(
                RawEvent(
                    source=source_key,
                    title=title,
                    url=url,
                    start_iso=_first_str(obj.get("startDate")),
                    end_iso=_first_str(obj.get("endDate")),
                    venue=_jsonld_location(obj.get("location")),
                    price_text=_jsonld_price(obj),
                    category_text=_first_str(obj.get("eventCategory") or obj.get("genre")),
                    image=absolutize(base_url, _first_str(obj.get("image"))),
                    description=_first_str(obj.get("description")),
                    extra={"strategy": "jsonld"},
                )
            )
    return events


# --- 2) __NEXT_DATA__ / gömülü JSON -----------------------------------------

_TITLE_KEYS = ("name", "title", "eventName", "event_name", "adi", "ad", "baslik")
_DATE_KEYS = (
    "startDate",
    "start_date",
    "date",
    "eventDate",
    "event_date",
    "startsAt",
    "start",
    "tarih",
    "baslangicTarihi",
    "beginDate",
    "showDate",
)
_VENUE_KEYS = ("venue", "venueName", "place", "location", "hall", "salon", "mekan", "yer")
_PRICE_KEYS = ("price", "minPrice", "min_price", "lowestPrice", "fiyat", "ucret", "amount")
_URL_KEYS = ("url", "link", "slug", "path", "seoUrl", "detailUrl")
_CATEGORY_KEYS = ("category", "categoryName", "type", "genre", "kategori", "tur")
_IMAGE_KEYS = ("image", "imageUrl", "img", "poster", "thumbnail", "gorsel")


def _pick(obj: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    """Sözlükten ilk dolu anahtarı (büyük/küçük harf duyarsız) okur."""
    lowered = {k.lower(): v for k, v in obj.items()}
    for key in keys:
        value = lowered.get(key.lower())
        got = _first_str(value)
        if got:
            return got
    return None


def _normalize_price_field(value: str | None) -> str | None:
    """Gömülü JSON'daki çıplak sayıya para birimi ekler ("450" -> "450 TL").

    Sıfır fiyat ücretsizlik demektir.
    """
    if value is None:
        return None
    stripped = value.strip()
    if re.fullmatch(r"\d+(?:[.,]\d+)?", stripped):
        return "Ücretsiz" if float(stripped.replace(",", ".")) == 0 else f"{stripped} TL"
    return value


def _looks_like_event(obj: dict[str, Any]) -> bool:
    """Bir JSON nesnesi etkinlik kaydına benziyor mu?

    Başlık + tarih benzeri iki alan yeterli sinyal. Tarih alanının gerçekten
    ayrıştırılabilir olması da aranır ki "created_at" gibi alanlar yanıltmasın.
    """
    if not isinstance(obj, dict) or len(obj) < 2:
        return False
    title = _pick(obj, _TITLE_KEYS)
    if not title or len(title) < 3 or len(title) > 200:
        return False
    date_text = _pick(obj, _DATE_KEYS)
    if not date_text:
        return False
    return parse_dates(date_text) is not None


def extract_nextdata(html: str, base_url: str, source_key: str) -> list[RawEvent]:
    """``__NEXT_DATA__`` (ve benzeri gömülü JSON) içinden etkinlikleri çıkarır."""
    tree = HTMLParser(html)
    payloads: list[str] = []

    for selector in ("script#__NEXT_DATA__", 'script[type="application/json"]'):
        for script in tree.css(selector):
            text = script.text()
            if text and len(text) > 40:
                payloads.append(text)

    # Nuxt / özel global durum nesneleri
    for match in re.finditer(
        r"(?:window\.__NUXT__|window\.__INITIAL_STATE__|window\.__DATA__)\s*=\s*({.*?})\s*;?\s*</script>",
        html,
        re.DOTALL,
    ):
        payloads.append(match.group(1))

    events: list[RawEvent] = []
    seen: set[str] = set()
    for payload in payloads:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for obj in _walk_json(data):
            if not _looks_like_event(obj):
                continue
            title = _pick(obj, _TITLE_KEYS)
            date_text = _pick(obj, _DATE_KEYS)
            assert title and date_text
            key = f"{title}|{date_text}"
            if key in seen:
                continue
            seen.add(key)
            events.append(
                RawEvent(
                    source=source_key,
                    title=title,
                    url=absolutize(base_url, _pick(obj, _URL_KEYS)),
                    start_iso=date_text,
                    end_iso=_pick(obj, ("endDate", "end_date", "endsAt", "bitisTarihi")),
                    venue=_pick(obj, _VENUE_KEYS),
                    price_text=_normalize_price_field(_pick(obj, _PRICE_KEYS)),
                    category_text=_pick(obj, _CATEGORY_KEYS),
                    image=absolutize(base_url, _pick(obj, _IMAGE_KEYS)),
                    extra={"strategy": "nextdata"},
                )
            )
    return events


# --- 3) CSS seçicileri -------------------------------------------------------


def extract_selectors(html: str, base_url: str, source_key: str, selectors: Any) -> list[RawEvent]:
    """``sources.yaml``'daki seçicilerle kart listesini ayrıştırır."""
    if not selectors.item:
        return []
    tree = HTMLParser(html)
    nodes: list[Node] = []
    for css, _ in parse_selector(selectors.item):
        try:
            found = tree.css(css)
        except ValueError:
            continue
        if found:
            nodes.extend(found)
    if not nodes:
        return []

    events: list[RawEvent] = []
    seen: set[str] = set()
    for node in nodes:
        title = pick_title(node, selectors.title or "")
        if not title:
            continue
        date_text = select_value(node, selectors.date) if selectors.date else None
        time_text = select_value(node, selectors.time) if selectors.time else None
        if not date_text and not time_text:
            # Kartın tümünde tarih ara (seçici tutmasa da metinde olabilir).
            blob = node_text(node)
            if parse_dates(blob) is None:
                continue
            date_text = blob[:200]
        key = f"{title}|{date_text}"
        if key in seen:
            continue
        seen.add(key)
        events.append(
            RawEvent(
                source=source_key,
                title=title,
                url=absolutize(base_url, select_value(node, selectors.url)),
                date_text=date_text,
                time_text=time_text,
                venue=venue_from_card(
                    node,
                    select_value(node, selectors.venue) if selectors.venue else None,
                    title,
                ),
                price_text=price_from_card(
                    node, select_value(node, selectors.price) if selectors.price else None
                ),
                category_text=select_value(node, selectors.category)
                if selectors.category
                else None,
                image=absolutize(base_url, select_value(node, selectors.image)),
                description=select_value(node, selectors.description)
                if selectors.description
                else None,
                extra={"strategy": "selectors"},
            )
        )
    return events


# --- 4) Sezgisel (yapı tanıma) ----------------------------------------------

_CARD_HINT_RE = re.compile(
    r"card|item|event|etkinlik|post|entry|tile|listing|result|box", re.IGNORECASE
)


def _signature(node: Node) -> str:
    """Bir düğümün 'yapısal imzası': etiket + sınıf adları.

    Aynı listedeki kartlar aynı imzayı paylaşır; en kalabalık imza grubu
    büyük olasılıkla etkinlik listesidir.
    """
    classes = node.attributes.get("class") or ""
    # Rastgele hash'lenmiş CSS module sınıflarını (ör. "Card_root__a1b2") sadeleştir.
    normalized = " ".join(sorted(re.sub(r"__[A-Za-z0-9]{4,}$", "", c) for c in classes.split()))
    return f"{node.tag}.{normalized}"


def extract_heuristic(
    html: str, base_url: str, source_key: str, *, min_items: int = 3
) -> list[RawEvent]:
    """Tekrar eden kart yapısını bularak etkinlik çıkarır (son çare).

    Bir düğüm aday sayılır ki: içinde link olsun, metni tarih içersin ve
    çok uzun olmasın (yani tüm sayfa değil, tek kart olsun).
    """
    tree = HTMLParser(html)
    candidates: list[Node] = []

    for node in tree.css("article, li, div, section"):
        classes = node.attributes.get("class") or ""
        text = node_text(node)
        if not (20 < len(text) < 600):
            continue
        if not node.css_first("a"):
            continue
        if not _CARD_HINT_RE.search(classes) and node.tag not in ("article", "li"):
            continue
        if parse_dates(text) is None:
            continue
        candidates.append(node)

    if not candidates:
        return []

    # En kalabalık yapısal imzayı seç.
    counts = Counter(_signature(n) for n in candidates)
    best_sig, best_count = counts.most_common(1)[0]
    if best_count < min_items:
        # Tek tip grup yoksa tüm adayları kullan ama iç içe olanları ele.
        chosen = candidates
    else:
        chosen = [n for n in candidates if _signature(n) == best_sig]

    # İç içe geçmiş adayları ayıkla: bir aday başka bir adayı içeriyorsa dıştakini at.
    inner_texts = [node_text(n) for n in chosen]
    keep: list[Node] = []
    for i, node in enumerate(chosen):
        contains_other = any(
            i != j and inner_texts[j] and inner_texts[j] in inner_texts[i]
            for j in range(len(chosen))
        )
        if not contains_other:
            keep.append(node)
    chosen = keep or chosen

    events: list[RawEvent] = []
    seen: set[str] = set()
    for node in chosen:
        link = node.css_first("a[href]")
        title = pick_title(node)
        if not title:
            continue

        text = node_text(node)
        # Tarihi taşıyan alt düğümü bulmaya çalış (daha temiz date_text için).
        date_text = text
        time_node = node.css_first("time[datetime]")
        if time_node:
            date_text = time_node.attributes.get("datetime") or text
        else:
            for child in node.css("span, div, p, small"):
                child_text = node_text(child)
                if 4 <= len(child_text) <= 60 and parse_dates(child_text):
                    date_text = child_text
                    break

        key = f"{title}|{date_text}"
        if key in seen:
            continue
        seen.add(key)
        img = node.css_first("img")
        events.append(
            RawEvent(
                source=source_key,
                title=title,
                url=absolutize(base_url, link.attributes.get("href") if link else None),
                date_text=date_text[:200],
                venue=venue_from_card(node, None, title),
                price_text=price_from_card(node, None),
                image=absolutize(
                    base_url,
                    (img.attributes.get("src") or img.attributes.get("data-src")) if img else None,
                ),
                extra={"strategy": "heuristic"},
            )
        )
    return events
