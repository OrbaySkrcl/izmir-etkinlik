"""Telegram komut ve buton işleyicileri."""

from __future__ import annotations

import contextlib
from typing import Any

import structlog
from telegram import Message, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..config import get_settings
from ..models import Category
from ..render import (
    esc,
    render_bucket,
    render_events,
    render_stats,
)
from ..store import repo
from ..store.db import session_scope
from ..util.dates import Bucket, today
from .keyboards import CB_ACTION, CB_BUCKET, CB_CATEGORY, category_menu, main_menu, settings_menu

log = structlog.get_logger(__name__)

WELCOME = """<b>İzmir Etkinlik Botu'na hoş geldiniz! 🎭</b>

İzmir'deki etkinlikleri birden fazla kaynaktan toplayıp tekilleştiriyorum;
aynı etkinliğin farklı sitelerdeki farklı yazımlarını tek kayda indiriyorum.

<b>Komutlar</b>
/bugun · /yarin · /haftasonu — yakın tarihler
/hafta · /geleceknafta · /ay — daha geniş aralık
/ucretsiz — sadece ücretsiz etkinlikler
/ara &lt;kelime&gt; — isme veya mekana göre arama
/kategori — türe göre listele
/abone — günlük bülteni aç/kapat
/ayarlar — bildirim tercihleri
/durum — bot ve kaynak durumu

Aşağıdaki butonlardan da hızlıca gezinebilirsiniz 👇"""

HELP = WELCOME

MAX_LIST = 40


async def _send(update: Update, messages: list[str], **kwargs) -> None:
    """Bir veya birden fazla mesajı sırayla gönderir."""
    target = update.effective_message
    if target is None:
        return
    for index, text in enumerate(messages):
        await target.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            **(kwargs if index == len(messages) - 1 else {}),
        )


async def _bucket_reply(update: Update, bucket: Bucket, *, free_only: bool = False) -> None:
    settings = get_settings()
    async with session_scope() as session:
        chat = update.effective_chat
        sub = await repo.get_subscriber(session, chat.id) if chat else None
        effective_free = free_only or bool(sub and sub.free_only)
        categories = list(sub.categories) if sub and sub.categories else None
        events = await repo.get_events_for_bucket(
            session,
            bucket,
            ref=today(),
            free_only=effective_free,
            categories=categories,
            limit=min(MAX_LIST, settings.max_events_per_message * 2),
        )
    await _send(
        update,
        render_bucket(events, bucket, free_only=effective_free),
        reply_markup=main_menu(effective_free),
    )


# --- komutlar ----------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat:
        async with session_scope() as session:
            await repo.upsert_subscriber(
                session, chat.id, title=chat.title or chat.full_name, active=True
            )
    await _send(update, [WELCOME], reply_markup=main_menu())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, [HELP], reply_markup=main_menu())


def _bucket_command(bucket: Bucket, free_only: bool = False):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _bucket_reply(update, bucket, free_only=free_only)

    return handler


async def cmd_free(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Önümüzdeki iki haftanın ücretsiz etkinlikleri."""
    async with session_scope() as session:
        events = await repo.get_events_for_bucket(
            session, Bucket.THIS_MONTH, free_only=True, limit=MAX_LIST
        )
    await _send(
        update,
        render_events(
            events,
            title="🆓 Ücretsiz Etkinlikler",
            empty_message="Şu an kayıtlı ücretsiz etkinlik yok.",
        ),
        reply_markup=main_menu(free_only=True),
    )


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ara <kelime> — başlık ve mekanda arama."""
    query = " ".join(context.args or []).strip()
    if not query:
        await _send(update, ["Aranacak kelimeyi yazın. Örnek: <code>/ara sezen aksu</code>"])
        return
    async with session_scope() as session:
        events = await repo.get_events(session, start=today(), query=query, limit=MAX_LIST)
    await _send(
        update,
        render_events(
            events,
            title=f"🔍 “{query}” için sonuçlar",
            group_by_day=False,
            numbered=True,
            empty_message="Eşleşen etkinlik bulunamadı. Farklı bir kelime deneyin.",
        ),
    )


async def cmd_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send(update, ["<b>🏷 Bir kategori seçin:</b>"], reply_markup=category_menu())


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Günlük bülteni aç/kapat."""
    chat = update.effective_chat
    if chat is None:
        return
    async with session_scope() as session:
        sub = await repo.get_subscriber(session, chat.id)
        new_state = not (sub.digest_enabled if sub else False)
        await repo.upsert_subscriber(
            session,
            chat.id,
            title=chat.title or chat.full_name,
            digest_enabled=new_state,
            active=True,
        )
    settings = get_settings()
    text = (
        f"✅ Günlük bülten açıldı. Her sabah {settings.digest_hour:02d}:"
        f"{settings.digest_minute:02d}'da o günün etkinliklerini göndereceğim."
        if new_state
        else "🔕 Günlük bülten kapatıldı. Tekrar açmak için /abone."
    )
    await _send(update, [text], reply_markup=main_menu())


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    async with session_scope() as session:
        sub = await repo.upsert_subscriber(session, chat.id, title=chat.title or chat.full_name)
        state = (sub.digest_enabled, sub.notify_new, sub.free_only)
    await _send(
        update,
        ["<b>⚙️ Bildirim Ayarları</b>\n\nDeğiştirmek için butonlara dokunun."],
        reply_markup=settings_menu(*state),
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        counts = await repo.count_events(session)
        categories = await repo.category_counts(session)
        run = await repo.last_run(session)
        health = await repo.source_health(session)

    last_run_text = None
    if run and run.finished_at:
        last_run_text = (
            f"Son tarama: {run.finished_at.strftime('%d.%m.%Y %H:%M')} UTC · "
            f"{run.raw_count} kayıt -> {run.unique_count} benzersiz "
            f"({run.duration_seconds:.0f} sn)"
        )
    text = render_stats(counts, categories, last_run_text)

    if health:
        lines = ["", "<b>Kaynaklar</b>"]
        for row in health:
            mark = "✅" if row.last_count > 0 else "⚠️"
            detail = f"{row.last_count} etkinlik"
            if row.consecutive_failures:
                detail += f" · {row.consecutive_failures} tur boş"
            lines.append(f"{mark} {row.source_key}: {detail}")
        text += "\n" + "\n".join(lines)
    await _send(update, [text])


async def cmd_scrape(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tara — sadece yöneticiler: elle tarama başlatır."""
    user = update.effective_user
    settings = get_settings()
    if not user or (settings.admin_ids and user.id not in settings.admin_ids):
        await _send(update, ["Bu komut yalnızca bot yöneticileri içindir."])
        return

    await _send(update, ["🔄 Tarama başlatıldı, bu birkaç dakika sürebilir…"])
    from ..pipeline import collect_and_store

    result = await collect_and_store(use_cache=False)
    await _send(update, [f"<pre>{esc(result.report())}</pre>"])


async def cmd_purge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/temizle — sadece yöneticiler: kayıtları silip yeniden tarar.

    Ayrıştırma düzeltmelerinden sonra eski/bozuk kayıtlar gelecek tarihli
    oldukları için kendiliğinden düşmez.
    """
    user = update.effective_user
    settings = get_settings()
    if not user or not settings.admin_ids or user.id not in settings.admin_ids:
        await _send(update, ["Bu komut yalnızca bot yöneticileri içindir."])
        return

    async with session_scope() as session:
        removed = await repo.delete_all_events(session)
    await _send(update, [f"🧹 {removed} kayıt silindi. Yeniden tarama başlıyor…"])

    from ..pipeline import collect_and_store

    result = await collect_and_store(use_cache=False)
    await _send(update, [f"<pre>{esc(result.report())}</pre>"])


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Komut olmayan mesajları arama olarak yorumlar."""
    text = (update.effective_message.text or "").strip() if update.effective_message else ""
    if not text or len(text) < 2:
        return
    context.args = text.split()
    await cmd_search(update, context)


# --- buton işleyicisi --------------------------------------------------------


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    await query.answer()
    parts = query.data.split(":")
    kind = parts[0]

    if kind == CB_BUCKET:
        bucket = Bucket(parts[1])
        free_only = len(parts) > 2 and parts[2] == "free"
        await _bucket_reply(update, bucket, free_only=free_only)
        return

    if kind == CB_CATEGORY:
        try:
            category = Category(parts[1])
        except ValueError:
            return
        async with session_scope() as session:
            events = await repo.get_events(
                session, start=today(), categories=[category.value], limit=MAX_LIST
            )
        await _send(
            update,
            render_events(
                events,
                title=f"{category.emoji} {category.label} Etkinlikleri",
                empty_message=f"Kayıtlı {category.label.lower()} etkinliği yok.",
            ),
            reply_markup=main_menu(),
        )
        return

    if kind == CB_ACTION:
        await _handle_action(update, parts[1:])


async def _handle_action(update: Update, args: list[str]) -> None:
    action = args[0] if args else ""
    chat = update.effective_chat
    # Çok eski mesajlar "erişilemez" gelir ve düzenlenemez; o durumda None sayılır.
    raw_message = update.callback_query.message if update.callback_query else None
    message = raw_message if isinstance(raw_message, Message) else None

    if action == "menu":
        if message:
            await message.edit_text(
                "<b>Ne görmek istersiniz?</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu(),
            )
        return

    if action == "categories":
        if message:
            await message.edit_text(
                "<b>🏷 Bir kategori seçin:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=category_menu(),
            )
        return

    if action == "toggle_free":
        free_only = bool(int(args[1])) if len(args) > 1 else False
        if message:
            label = (
                "🆓 Sadece ücretsiz etkinlikler gösteriliyor."
                if free_only
                else "Tüm etkinlikler gösteriliyor."
            )
            await message.edit_text(
                f"<b>{label}</b>\n\nBir tarih aralığı seçin:",
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu(free_only),
            )
        return

    if action in {"set_digest", "set_notify", "set_freeonly"} and chat:
        value = bool(int(args[1])) if len(args) > 1 else False
        field = {
            "set_digest": "digest_enabled",
            "set_notify": "notify_new",
            "set_freeonly": "free_only",
        }[action]
        updates: dict[str, Any] = {field: value}
        async with session_scope() as session:
            sub = await repo.upsert_subscriber(session, chat.id, **updates)
            state = (sub.digest_enabled, sub.notify_new, sub.free_only)
        if message:
            await message.edit_text(
                "<b>⚙️ Bildirim Ayarları</b>\n\nDeğiştirmek için butonlara dokunun.",
                parse_mode=ParseMode.HTML,
                reply_markup=settings_menu(*state),
            )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Beklenmeyen hataları loglar; kullanıcıya kısa bilgi verir."""
    error = context.error
    if isinstance(error, Forbidden):
        # Kullanıcı botu engellemiş: aboneliği pasifleştir.
        chat = getattr(update, "effective_chat", None)
        if chat:
            async with session_scope() as session:
                await repo.deactivate_subscriber(session, chat.id)
        return
    log.exception("bot_error", error=str(error))
    if isinstance(update, Update) and update.effective_message:
        # Bildirim de gönderilemiyorsa sessizce geç: hata döngüsü yaratma.
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text(
                "Bir hata oluştu, kayda geçtim. Lütfen tekrar deneyin."
            )


def register(app: Application) -> None:
    """Tüm işleyicileri uygulamaya bağlar."""
    app.add_handler(CommandHandler(["start", "basla"], cmd_start))
    app.add_handler(CommandHandler(["help", "yardim"], cmd_help))
    app.add_handler(CommandHandler(["bugun", "today"], _bucket_command(Bucket.TODAY)))
    app.add_handler(CommandHandler(["yarin"], _bucket_command(Bucket.TOMORROW)))
    app.add_handler(CommandHandler(["haftasonu"], _bucket_command(Bucket.WEEKEND)))
    app.add_handler(CommandHandler(["hafta"], _bucket_command(Bucket.THIS_WEEK)))
    app.add_handler(
        CommandHandler(["geleceknafta", "gelecekhafta"], _bucket_command(Bucket.NEXT_WEEK))
    )
    app.add_handler(CommandHandler(["ay"], _bucket_command(Bucket.THIS_MONTH)))
    app.add_handler(CommandHandler(["ileride"], _bucket_command(Bucket.LATER)))
    app.add_handler(CommandHandler(["ucretsiz", "bedava"], cmd_free))
    app.add_handler(CommandHandler(["ara", "search"], cmd_search))
    app.add_handler(CommandHandler(["kategori", "kategoriler"], cmd_categories))
    app.add_handler(CommandHandler(["abone", "bulten"], cmd_subscribe))
    app.add_handler(CommandHandler(["ayarlar", "settings"], cmd_settings))
    app.add_handler(CommandHandler(["durum", "status"], cmd_status))
    app.add_handler(CommandHandler(["tara", "scrape"], cmd_scrape))
    app.add_handler(CommandHandler(["temizle", "purge"], cmd_purge))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
