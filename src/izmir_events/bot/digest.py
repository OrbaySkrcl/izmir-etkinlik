"""Zamanlanmış işler: tarama, günlük bülten, yeni etkinlik bildirimi."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import structlog
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import ContextTypes

from ..config import get_settings
from ..models import Event
from ..pipeline import collect_and_store
from ..render import render_digest, render_events, render_new_events
from ..store import repo
from ..store.db import session_scope
from ..store.models import Subscriber
from ..util.dates import Bucket, today

log = structlog.get_logger(__name__)

# Telegram yayın hızı sınırı: saniyede ~30 mesaj. Güvenli tarafta kalalım.
BROADCAST_DELAY = 0.05


def _matches(event: Event, sub: Subscriber) -> bool:
    """Abonenin filtrelerine uyuyor mu?"""
    if sub.free_only and not event.is_free:
        return False
    if sub.categories and event.category.value not in sub.categories:
        return False
    if sub.keywords:
        blob = f"{event.norm_title} {event.norm_venue}"
        if not any(k.lower() in blob for k in sub.keywords):
            return False
    return True


async def _send_to(context: ContextTypes.DEFAULT_TYPE, chat_id: int, messages: list[str]) -> bool:
    """Bir aboneye mesaj gönderir. Engellenmişse aboneliği pasifleştirir."""
    for text in messages:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Forbidden:
            log.info("subscriber_blocked_bot", chat_id=chat_id)
            async with session_scope() as session:
                await repo.deactivate_subscriber(session, chat_id)
            return False
        except RetryAfter as exc:
            # PTB sürümüne göre retry_after int saniye veya timedelta olabilir.
            wait = exc.retry_after
            seconds = wait.total_seconds() if isinstance(wait, timedelta) else float(wait)
            await asyncio.sleep(seconds + 1)
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except TelegramError as retry_exc:
                log.warning("send_failed_after_retry", chat_id=chat_id, error=str(retry_exc))
                return False
        except TelegramError as exc:
            log.warning("send_failed", chat_id=chat_id, error=str(exc))
            return False
        await asyncio.sleep(BROADCAST_DELAY)
    return True


async def job_scrape(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periyodik tarama; yeni etkinlik bulursa ilgili abonelere haber verir."""
    try:
        result = await collect_and_store()
    except Exception:
        log.exception("scrape_job_failed")
        return

    log.info("scrape_job_done", unique=len(result.events), new=result.inserted)

    if result.failed_sources:
        log.warning(
            "sources_returned_nothing",
            sources=[s.key for s in result.failed_sources],
        )
    if result.inserted:
        await announce_new_events(context)


async def announce_new_events(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Henüz duyurulmamış etkinlikleri abonelere gönderir."""
    async with session_scope() as session:
        new_events = await repo.get_new_events(session, limit=30)
        subscribers = await repo.list_subscribers(session, notify_only=True)

    if not new_events or not subscribers:
        # Duyuru yapılmasa da işaretle: eski kayıtlar birikmesin.
        if new_events:
            async with session_scope() as session:
                await repo.mark_announced(session, [e.uid() for e in new_events])
        return

    ref = today()
    sent_any = False
    for sub in subscribers:
        relevant = [e for e in new_events if _matches(e, sub)]
        if not relevant:
            continue
        # Çok fazla yeni kayıt varsa mesajı kısa tut.
        shown = relevant[:12]
        messages = render_new_events(shown, ref=ref)
        if len(relevant) > len(shown):
            messages[-1] += f"\n\n<i>…ve {len(relevant) - len(shown)} etkinlik daha. /hafta</i>"
        if await _send_to(context, sub.chat_id, messages):
            sent_any = True

    async with session_scope() as session:
        await repo.mark_announced(session, [e.uid() for e in new_events])
    log.info("new_events_announced", count=len(new_events), delivered=sent_any)


async def job_daily_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Her sabah abonelere o günün programını gönderir."""
    ref = today()
    async with session_scope() as session:
        subscribers = await repo.list_subscribers(session, digest_only=True)
        events = await repo.get_events_for_bucket(session, Bucket.TODAY, ref=ref, limit=40)
        free_events = [e for e in events if e.is_free]

    if not subscribers:
        return

    for sub in subscribers:
        relevant = [e for e in events if _matches(e, sub)]
        if sub.free_only:
            messages = render_events(
                relevant,
                title="🆓 Bugün İzmir'de ücretsiz",
                ref=ref,
                group_by_day=False,
                empty_message="Bugün için kayıtlı ücretsiz etkinlik yok.",
            )
        else:
            messages = render_digest(relevant, ref=ref, free_count=len(free_events))
        await _send_to(context, sub.chat_id, messages)

    log.info("digest_sent", subscribers=len(subscribers), events=len(events))


async def job_health_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kaynaklar üst üste boş dönerse yöneticilere uyarı gönderir.

    Scraper'ın sessizce bozulması en sinsi arıza türü: bot çalışmaya devam
    eder ama etkinlik gelmez. Bu iş onu görünür kılar.
    """
    settings = get_settings()
    if not settings.admin_ids:
        return
    async with session_scope() as session:
        health = await repo.source_health(session)

    broken = [h for h in health if h.consecutive_failures >= 3]
    if not broken:
        return

    lines = ["⚠️ <b>Kaynak uyarısı</b>", "", "Şu kaynaklar üst üste boş dönüyor:"]
    for row in broken:
        last_ok = row.last_ok_at.strftime("%d.%m.%Y") if row.last_ok_at else "hiç"
        lines.append(
            f"• <code>{row.source_key}</code> — {row.consecutive_failures} tur "
            f"(son başarı: {last_ok})"
        )
        if row.last_error:
            lines.append(f"  <i>{row.last_error[:120]}</i>")
    lines += [
        "",
        "Seçicileri kalibre etmek için:\n"
        "<code>izmir-etkinlik doctor --source &lt;anahtar&gt;</code>",
    ]

    for admin_id in settings.admin_ids:
        await _send_to(context, admin_id, ["\n".join(lines)])
