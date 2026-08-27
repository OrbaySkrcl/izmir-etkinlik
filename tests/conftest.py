"""Ortak test düzenekleri."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio

from izmir_events.config import Settings
from izmir_events.models import RawEvent, build_event
from izmir_events.store import db as db_module

FIXTURES = Path(__file__).parent / "fixtures"

# Testlerde sabit "bugün": 27 Ağustos 2026, Perşembe.
REF = date(2026, 8, 27)


@pytest.fixture
def ref() -> date:
    return REF


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


def make_event(
    source: str = "test",
    title: str = "Test Etkinliği",
    date_text: str = "12 Eylül 2026",
    venue: str | None = None,
    price_text: str | None = None,
    url: str | None = None,
    ref_date: date = REF,
    free_by_default: bool = False,
):
    """Test için hızlı ``Event`` üretici."""
    event = build_event(
        RawEvent(
            source=source,
            title=title,
            date_text=date_text,
            venue=venue,
            price_text=price_text,
            url=url or f"https://{source}.test/{abs(hash(title)) % 10000}",
        ),
        free_by_default=free_by_default,
        ref=ref_date,
    )
    assert event is not None, f"tarih ayrıştırılamadı: {date_text}"
    return event


@pytest.fixture
def event_factory():
    return make_event


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    """Her test için izole SQLite veritabanı."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    db_module.reset_engine_for_tests(url)
    await db_module.init_db(url)
    yield db_module
    await db_module.dispose_db()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        telegram_bot_token="test:token",
        http_delay_seconds=0.0,
        http_max_retries=1,
        respect_robots=False,
        http_cache_ttl_minutes=0,
        sources_file=str(Path(__file__).parents[1] / "config" / "sources.yaml"),
    )
