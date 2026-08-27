"""Telegram uygulamasının kurulumu ve çalıştırılması."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import structlog
from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder, Defaults

from ..config import Settings, get_settings
from ..store.db import dispose_db, init_db
from . import digest, handlers

log = structlog.get_logger(__name__)

BOT_COMMANDS = [
    BotCommand("bugun", "Bugünkü etkinlikler"),
    BotCommand("yarin", "Yarınki etkinlikler"),
    BotCommand("haftasonu", "Bu hafta sonu"),
    BotCommand("hafta", "Bu hafta"),
    BotCommand("gelecekhafta", "Gelecek hafta"),
    BotCommand("ay", "Bu ay"),
    BotCommand("ucretsiz", "Ücretsiz etkinlikler"),
    BotCommand("ara", "Etkinlik ara"),
    BotCommand("kategori", "Kategoriye göre listele"),
    BotCommand("abone", "Günlük bülteni aç/kapat"),
    BotCommand("ayarlar", "Bildirim tercihleri"),
    BotCommand("durum", "Bot ve kaynak durumu"),
    BotCommand("yardim", "Yardım"),
]


async def _post_init(app: Application) -> None:
    await init_db()
    await app.bot.set_my_commands(BOT_COMMANDS)
    log.info("bot_ready", username=(await app.bot.get_me()).username)


async def _post_shutdown(app: Application) -> None:
    await dispose_db()


def schedule_jobs(app: Application, settings: Settings) -> None:
    """Periyodik işleri kurar."""
    queue = app.job_queue
    if queue is None:  # pragma: no cover - job-queue eklentisi yoksa
        log.warning("job_queue_unavailable")
        return

    tz = ZoneInfo(settings.timezone)

    if settings.scrape_interval_minutes > 0:
        queue.run_repeating(
            digest.job_scrape,
            interval=dt.timedelta(minutes=settings.scrape_interval_minutes),
            first=dt.timedelta(seconds=20)
            if settings.scrape_on_startup
            else dt.timedelta(minutes=settings.scrape_interval_minutes),
            name="scrape",
        )

    queue.run_daily(
        digest.job_daily_digest,
        time=dt.time(hour=settings.digest_hour, minute=settings.digest_minute, tzinfo=tz),
        name="daily_digest",
    )

    # Kaynak sağlığı raporu: her gün öğlen.
    queue.run_daily(
        digest.job_health_report,
        time=dt.time(hour=12, minute=0, tzinfo=tz),
        name="health_report",
    )
    log.info(
        "jobs_scheduled",
        scrape_every_minutes=settings.scrape_interval_minutes,
        digest_at=f"{settings.digest_hour:02d}:{settings.digest_minute:02d} {settings.timezone}",
    )


def build_application(settings: Settings | None = None) -> Application:
    """Yapılandırılmış Telegram uygulamasını üretir."""
    settings = settings or get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN tanımlı değil. .env dosyanıza ekleyin "
            "(BotFather'dan alabilirsiniz)."
        )

    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .defaults(Defaults(tzinfo=ZoneInfo(settings.timezone)))
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .concurrent_updates(True)
        .build()
    )
    handlers.register(app)
    schedule_jobs(app, settings)
    return app


def run(settings: Settings | None = None) -> None:
    """Botu çalıştırır (webhook veya long polling)."""
    settings = settings or get_settings()
    app = build_application(settings)

    if settings.use_webhook:
        url = settings.webhook_url.rstrip("/")
        path = settings.telegram_bot_token.split(":")[-1][:24]
        log.info("starting_webhook", url=f"{url}/{path}", port=settings.port)
        app.run_webhook(
            listen="0.0.0.0",
            port=settings.port,
            url_path=path,
            webhook_url=f"{url}/{path}",
            secret_token=settings.webhook_secret or None,
            drop_pending_updates=True,
        )
    else:
        log.info("starting_polling")
        app.run_polling(drop_pending_updates=True)
