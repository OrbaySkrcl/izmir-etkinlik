"""Etkinlikleri Telegram mesajına dönüştürür.

Telegram HTML parse_mode kullanılır (Markdown'a göre kaçış kuralları daha
öngörülebilir). Mesajlar 4096 karakter sınırına göre parçalanır.
"""

from __future__ import annotations

import html
from datetime import date, time, timedelta

from .models import Category, Event
from .util.dates import (
    MONTH_NAMES_TR,
    WEEKDAY_NAMES_TR,
    Bucket,
    format_dates,
    today,
)
from .util.text import truncate

TELEGRAM_LIMIT = 4096
# Başlık/altbilgi için pay bırak.
SAFE_LIMIT = 3800


def esc(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def event_line(
    event: Event, *, ref: date | None = None, show_date: bool = True, index: int | None = None
) -> str:
    """Tek etkinliğin listedeki satırı."""
    ref = ref or today()
    prefix = f"{index}. " if index is not None else ""
    title = esc(truncate(event.title, 90))
    url = event.primary_url()
    heading = f'<a href="{esc(url)}">{title}</a>' if url else f"<b>{title}</b>"

    parts = [f"{prefix}{event.category.emoji} {heading}"]

    meta: list[str] = []
    if show_date:
        meta.append(format_dates(event.dates, ref=ref))
    elif event.start_time:
        meta.append(event.start_time.strftime("%H:%M"))
    if event.venue:
        meta.append(f"📍 {esc(truncate(event.venue, 45))}")
    if meta:
        parts.append("   " + " · ".join(meta))

    tail: list[str] = []
    price = event.price
    tail.append("🆓 <b>Ücretsiz</b>" if price.is_free else f"🎟 {esc(price.label())}")
    if event.source_count > 1:
        tail.append(f"🔗 {event.source_count} kaynak")
    parts.append("   " + " · ".join(tail))

    return "\n".join(parts)


def _group_by_day(events: list[Event]) -> dict[date, list[Event]]:
    grouped: dict[date, list[Event]] = {}
    for event in sorted(events, key=lambda e: (e.start, e.start_time or time.min, e.title)):
        grouped.setdefault(event.start, []).append(event)
    return grouped


def day_heading(day: date, ref: date) -> str:
    """Gün başlığı: "📅 Bugün — 27 Ağustos Perşembe"."""
    label = f"{day.day} {MONTH_NAMES_TR[day.month]} {WEEKDAY_NAMES_TR[day.weekday()]}"
    if day == ref:
        label = f"Bugün — {label}"
    elif day == ref + timedelta(days=1):
        label = f"Yarın — {label}"
    elif day < ref:
        # Çok günlü etkinlikler geçmiş bir günde başlamış olabilir.
        label = f"Devam eden — {label} başlangıçlı"
    return f"<b>📅 {label}</b>"


def _pack(
    header: str, blocks: list[tuple[str | None, list[str]]], footer: str | None = None
) -> list[str]:
    """Satırları Telegram sınırına sığan mesajlara paketler.

    Bölme yalnızca bloklar arasında değil, blok *içinde* de yapılır: tek bir
    günde 100 etkinlik varsa o gün de birden fazla mesaja bölünür ve devam
    mesajında gün başlığı tekrarlanır. Aksi halde mesaj 4096 karakter
    sınırını aşar ve Telegram gönderimi tümüyle reddeder.
    """
    messages: list[str] = []
    parts: list[str] = [header]
    length = len(header)
    continuation = False

    def flush() -> None:
        nonlocal parts, length, continuation
        if parts:
            messages.append("\n".join(parts))
        parts = []
        length = 0
        continuation = True

    for heading, lines in blocks:
        if heading:
            if length + len(heading) + 2 > SAFE_LIMIT:
                flush()
                parts = [f"{header} <i>(devam)</i>"]
                length = len(parts[0])
            parts.append("")
            parts.append(heading)
            length += len(heading) + 2
        for line in lines:
            piece = truncate_line(line)
            if length + len(piece) + 1 > SAFE_LIMIT:
                flush()
                parts = [f"{header} <i>(devam)</i>"]
                length = len(parts[0])
                if heading:
                    parts += ["", f"{heading} <i>(devam)</i>"]
                    length += len(heading) + 12
            parts.append(piece)
            length += len(piece) + 1

    if footer:
        text = esc(footer)
        if length + len(text) + 2 > SAFE_LIMIT:
            flush()
            parts = [text]
        else:
            parts += ["", text]

    if parts:
        messages.append("\n".join(parts))
    return messages or [header]


def truncate_line(line: str) -> str:
    """Tek bir satır bile sınırı aşıyorsa (aşırı uzun başlık) kısaltır."""
    if len(line) <= SAFE_LIMIT - 200:
        return line
    return line[: SAFE_LIMIT - 200] + "…"


def render_events(
    events: list[Event],
    *,
    title: str,
    ref: date | None = None,
    group_by_day: bool = True,
    numbered: bool = False,
    empty_message: str | None = None,
    footer: str | None = None,
) -> list[str]:
    """Etkinlik listesini bir veya birden fazla Telegram mesajına çevirir."""
    ref = ref or today()
    header = f"<b>{esc(title)}</b>"

    if not events:
        return [f"{header}\n\n{esc(empty_message or 'Bu aralıkta etkinlik bulunamadı.')}"]

    blocks: list[tuple[str | None, list[str]]] = []
    if group_by_day:
        for day, day_events in _group_by_day(events).items():
            blocks.append(
                (
                    day_heading(day, ref),
                    [event_line(e, ref=ref, show_date=e.dates.multi_day) for e in day_events],
                )
            )
    else:
        blocks.append(
            (
                None,
                [
                    event_line(e, ref=ref, index=(i + 1) if numbered else None)
                    for i, e in enumerate(events)
                ],
            )
        )

    return _pack(header, blocks, footer)


def render_bucket(
    events: list[Event], bucket: Bucket, *, ref: date | None = None, free_only: bool = False
) -> list[str]:
    """Bir tarih kovası için başlıklı liste üretir."""
    ref = ref or today()
    title = f"{bucket.label} İzmir'de"
    if free_only:
        title = f"{bucket.label} — Ücretsiz Etkinlikler"
    empty = {
        Bucket.TODAY: "Bugün için kayıtlı etkinlik yok. /hafta ile bu haftaya bakabilirsiniz.",
        Bucket.TOMORROW: "Yarın için kayıtlı etkinlik yok.",
        Bucket.WEEKEND: "Bu hafta sonu için kayıtlı etkinlik yok.",
    }.get(bucket, "Bu aralıkta etkinlik bulunamadı.")
    if free_only:
        empty = "Bu aralıkta ücretsiz etkinlik bulunamadı."
    return render_events(events, title=title, ref=ref, empty_message=empty)


def render_digest(
    events: list[Event], *, ref: date | None = None, free_count: int = 0
) -> list[str]:
    """Günlük bülten mesajı."""
    ref = ref or today()
    label = f"{ref.day} {MONTH_NAMES_TR[ref.month]} {WEEKDAY_NAMES_TR[ref.weekday()]}"
    title = f"🌅 Günaydın! {label} — İzmir'de bugün"
    footer = None
    if free_count:
        footer = f"Bugünkü {free_count} etkinlik ücretsiz. Tümü için: /ucretsiz"
    return render_events(
        events,
        title=title,
        ref=ref,
        group_by_day=False,
        empty_message="Bugün için kayıtlı etkinlik yok. Yarına bakmak için /yarin.",
        footer=footer,
    )


def render_new_events(events: list[Event], *, ref: date | None = None) -> list[str]:
    """Yeni tespit edilen etkinlik bildirimi."""
    ref = ref or today()
    count = len(events)
    title = f"✨ {count} yeni etkinlik eklendi"
    return render_events(events, title=title, ref=ref, group_by_day=False)


def render_event_detail(event: Event, *, ref: date | None = None) -> str:
    """Tek etkinliğin ayrıntı kartı."""
    ref = ref or today()
    lines = [f"{event.category.emoji} <b>{esc(event.title)}</b>", ""]
    lines.append(f"🗓 {esc(format_dates(event.dates, ref=ref))}")
    if event.venue:
        lines.append(f"📍 {esc(event.venue)}")
    lines.append(f"🏷 {esc(event.category.label)}")
    lines.append("🆓 <b>Ücretsiz</b>" if event.is_free else f"🎟 {esc(event.price.label())}")
    if event.description:
        lines += ["", esc(truncate(event.description, 400))]
    if event.sources:
        lines += ["", "<b>Kaynaklar:</b>"]
        for key, url in sorted(event.sources.items()):
            name = esc(key)
            lines.append(f'• <a href="{esc(url)}">{name}</a>' if url else f"• {name}")
    if len(event.source_titles) > 1:
        variants = {t for t in event.source_titles.values() if t != event.title}
        if variants:
            lines += ["", "<i>Diğer kaynaklardaki adlandırmalar:</i>"]
            lines += [f"<i>· {esc(truncate(v, 70))}</i>" for v in sorted(variants)[:4]]
    return "\n".join(lines)


def render_stats(
    counts: dict[str, int], categories: dict[str, int], last_run_text: str | None = None
) -> str:
    """/durum komutunun çıktısı."""
    lines = [
        "<b>📊 Bot Durumu</b>",
        "",
        f"Gelecek etkinlik: <b>{counts.get('gelecek', 0)}</b>",
        f"Bunlardan ücretsiz: <b>{counts.get('ucretsiz', 0)}</b>",
        f"Birden fazla kaynakta doğrulanan: <b>{counts.get('cok_kaynakli', 0)}</b>",
        f"Veritabanındaki toplam kayıt: {counts.get('toplam', 0)}",
    ]
    if categories:
        lines += ["", "<b>Kategoriler</b>"]
        for key, count in list(categories.items())[:10]:
            try:
                category = Category(key)
                lines.append(f"{category.emoji} {category.label}: {count}")
            except ValueError:
                lines.append(f"• {esc(key)}: {count}")
    if last_run_text:
        lines += ["", f"<i>{esc(last_run_text)}</i>"]
    return "\n".join(lines)
