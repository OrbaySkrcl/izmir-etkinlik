"""Bot kurulumu ve zamanlanmış iş testleri (ağa çıkmaz)."""

import re
from datetime import date
from types import SimpleNamespace

import pytest

from izmir_events.bot import digest as digest_module
from izmir_events.bot.app import BOT_COMMANDS, build_application
from izmir_events.bot.keyboards import category_menu, main_menu, settings_menu
from izmir_events.store import repo
from izmir_events.store.db import session_scope
from izmir_events.store.models import Subscriber

REF = date(2026, 8, 27)


class TestApplicationSetup:
    def test_builds_and_registers_handlers(self, settings):
        app = build_application(settings)
        assert len(app.handlers[0]) > 15
        assert app.job_queue is not None

    def test_missing_token_raises_clear_error(self, settings):
        broken = settings.model_copy(update={"telegram_bot_token": ""})
        with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
            build_application(broken)

    def test_scheduled_jobs_registered(self, settings):
        app = build_application(settings)
        names = {job.name for job in app.job_queue.jobs()}
        assert {"scrape", "daily_digest", "health_report"} <= names

    def test_bot_commands_are_valid(self):
        # Telegram komut adları: küçük harf, 1-32 karakter, sadece a-z0-9_
        for command in BOT_COMMANDS:
            assert re.fullmatch(r"[a-z0-9_]{1,32}", command.command), command.command
            assert 1 <= len(command.description) <= 256


class TestKeyboards:
    def test_callback_data_within_telegram_limit(self):
        for row in main_menu().inline_keyboard:
            for button in row:
                assert len(button.callback_data.encode()) <= 64

    def test_free_toggle_changes_label(self):
        normal = main_menu(free_only=False).inline_keyboard[-1][0].text
        free = main_menu(free_only=True).inline_keyboard[-1][0].text
        assert normal != free

    def test_category_menu_has_back_button(self):
        assert "Geri" in category_menu().inline_keyboard[-1][0].text

    def test_settings_menu_reflects_state(self):
        assert settings_menu(True, True, True).inline_keyboard[0][0].text.startswith("✅")
        assert settings_menu(False, True, True).inline_keyboard[0][0].text.startswith("❌")


class TestSubscriberFilters:
    def _sub(self, **kwargs) -> Subscriber:
        defaults = {"chat_id": 1, "free_only": False, "categories": [], "keywords": []}
        defaults.update(kwargs)
        return Subscriber(**defaults)

    def test_free_only_filter(self, event_factory):
        paid = event_factory("bubilet", "Konser", "12 Eylül 2026", price_text="200 TL")
        free = event_factory("kultursanat", "Konser", "12 Eylül 2026", price_text="Ücretsiz")
        sub = self._sub(free_only=True)
        assert not digest_module._matches(paid, sub)
        assert digest_module._matches(free, sub)

    def test_category_filter(self, event_factory):
        concert = event_factory("bubilet", "Bir Konser", "12 Eylül 2026")
        assert not digest_module._matches(concert, self._sub(categories=["tiyatro"]))
        assert digest_module._matches(concert, self._sub(categories=["konser"]))

    def test_keyword_filter(self, event_factory):
        event = event_factory("bubilet", "Sezen Aksu", "12 Eylül 2026")
        assert digest_module._matches(event, self._sub(keywords=["sezen"]))
        assert not digest_module._matches(event, self._sub(keywords=["tarkan"]))

    def test_no_filters_matches_everything(self, event_factory):
        event = event_factory("bubilet", "Herhangi", "12 Eylül 2026")
        assert digest_module._matches(event, self._sub())


class FakeBot:
    """send_message çağrılarını kaydeden sahte bot."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


class TestJobs:
    async def test_digest_sent_to_subscribers_only(self, db, event_factory, monkeypatch):
        async with session_scope() as session:
            await repo.upsert_events(
                session, [event_factory("bubilet", "Bugünkü Konser", "27 Ağustos 2026")]
            )
            await repo.upsert_subscriber(session, 111, digest_enabled=True)
            await repo.upsert_subscriber(session, 222, digest_enabled=False)

        monkeypatch.setattr(digest_module, "today", lambda: REF)
        bot = FakeBot()
        await digest_module.job_daily_digest(SimpleNamespace(bot=bot))

        assert {chat_id for chat_id, _ in bot.sent} == {111}
        assert "Günaydın" in bot.sent[0][1]

    async def test_new_events_announced_once(self, db, event_factory, monkeypatch):
        async with session_scope() as session:
            await repo.upsert_events(
                session, [event_factory("bubilet", "Yeni Konser", "12 Eylül 2026")]
            )
            await repo.upsert_subscriber(session, 111, notify_new=True)

        monkeypatch.setattr(digest_module, "today", lambda: REF)
        bot = FakeBot()
        await digest_module.announce_new_events(SimpleNamespace(bot=bot))
        first_count = len(bot.sent)
        await digest_module.announce_new_events(SimpleNamespace(bot=bot))

        assert first_count == 1
        assert len(bot.sent) == 1  # ikinci turda tekrar duyurulmamalı

    async def test_health_report_only_for_broken_sources(self, db, settings, monkeypatch):
        monkeypatch.setattr(
            digest_module,
            "get_settings",
            lambda: settings.model_copy(update={"telegram_admin_ids": "999"}),
        )
        async with session_scope() as session:
            for _ in range(3):
                await repo.update_source_health(
                    session, "bozuk", count=0, strategy=None, error="HTTP 500"
                )
            await repo.update_source_health(
                session, "saglikli", count=10, strategy="jsonld", error=None
            )

        bot = FakeBot()
        await digest_module.job_health_report(SimpleNamespace(bot=bot))

        assert len(bot.sent) == 1
        chat_id, text = bot.sent[0]
        assert chat_id == 999
        assert "bozuk" in text
        assert "saglikli" not in text

    async def test_no_health_report_when_all_ok(self, db, settings, monkeypatch):
        monkeypatch.setattr(
            digest_module,
            "get_settings",
            lambda: settings.model_copy(update={"telegram_admin_ids": "999"}),
        )
        async with session_scope() as session:
            await repo.update_source_health(
                session, "saglikli", count=10, strategy="jsonld", error=None
            )

        bot = FakeBot()
        await digest_module.job_health_report(SimpleNamespace(bot=bot))
        assert bot.sent == []
