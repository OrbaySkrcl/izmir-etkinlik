"""Veritabanı katmanı testleri."""

from datetime import date

import pytest

from izmir_events.store import repo
from izmir_events.store.db import session_scope
from izmir_events.util.dates import Bucket

REF = date(2026, 8, 27)


class TestUpsert:
    async def test_insert_then_update_keeps_first_seen(self, db, event_factory):
        event = event_factory("bubilet", "Sezen Aksu", "12 Eylül 2026", "Kültürpark")
        async with session_scope() as session:
            first = await repo.upsert_events(session, [event])
        assert first.inserted == 1
        assert first.new_uids == [event.uid()]

        async with session_scope() as session:
            second = await repo.upsert_events(session, [event])
        assert second.inserted == 0
        assert second.updated == 1

    async def test_empty_list(self, db):
        async with session_scope() as session:
            result = await repo.upsert_events(session, [])
        assert result.inserted == 0

    async def test_roundtrip_preserves_fields(self, db, event_factory):
        event = event_factory(
            "bubilet", "Sezen Aksu", "12 Eylül 2026 21:00", "Kültürpark", "450 TL - 900 TL"
        )
        async with session_scope() as session:
            await repo.upsert_events(session, [event])
            stored = await repo.get_events(session, start=REF, limit=5)
        assert len(stored) == 1
        got = stored[0]
        assert got.title == event.title
        assert got.venue == event.venue
        assert got.price_min == 450.0
        assert got.price_max == 900.0
        assert got.start_time == event.start_time
        assert got.sources == event.sources


class TestQueries:
    @pytest.fixture
    async def seeded(self, db, event_factory):
        events = [
            event_factory(
                "kultursanat", "Bugünkü Konser", "27 Ağustos 2026", "İzmir Sanat", "Ücretsiz"
            ),
            event_factory(
                "bubilet", "Yarınki Tiyatro", "28 Ağustos 2026", "Konak Sahnesi", "200 TL"
            ),
            event_factory(
                "bubilet", "Gelecek Hafta Konseri", "3 Eylül 2026", "Arena İzmir", "500 TL"
            ),
            event_factory(
                "kultursanat",
                "Süren Sergi",
                "1 Ağustos - 30 Ekim 2026",
                "Arkas Sanat Merkezi",
                "Ücretsiz",
            ),
        ]
        async with session_scope() as session:
            await repo.upsert_events(session, events)
        return events

    async def test_today_bucket_includes_ongoing_exhibition(self, seeded):
        async with session_scope() as session:
            events = await repo.get_events_for_bucket(session, Bucket.TODAY, ref=REF)
        titles = {e.title for e in events}
        assert "Bugünkü Konser" in titles
        assert "Süren Sergi" in titles  # çok günlü, bugünü kapsıyor
        assert "Yarınki Tiyatro" not in titles

    async def test_this_week_bucket(self, seeded):
        async with session_scope() as session:
            events = await repo.get_events_for_bucket(session, Bucket.THIS_WEEK, ref=REF)
        assert "Gelecek Hafta Konseri" not in {e.title for e in events}

    async def test_next_week_bucket(self, seeded):
        async with session_scope() as session:
            events = await repo.get_events_for_bucket(session, Bucket.NEXT_WEEK, ref=REF)
        assert "Gelecek Hafta Konseri" in {e.title for e in events}

    async def test_free_only_filter(self, seeded):
        async with session_scope() as session:
            events = await repo.get_events(session, start=REF, free_only=True, limit=20)
        assert all(e.is_free for e in events)
        assert len(events) == 2

    async def test_category_filter(self, seeded):
        async with session_scope() as session:
            events = await repo.get_events(session, start=REF, categories=["sergi"], limit=20)
        assert {e.title for e in events} == {"Süren Sergi"}

    async def test_search_is_accent_and_case_insensitive(self, seeded):
        async with session_scope() as session:
            events = await repo.get_events(session, start=REF, query="SERGI", limit=20)
        assert "Süren Sergi" in {e.title for e in events}

    async def test_search_matches_venue(self, seeded):
        async with session_scope() as session:
            events = await repo.get_events(session, start=REF, query="arena", limit=20)
        assert {e.title for e in events} == {"Gelecek Hafta Konseri"}

    async def test_search_no_match(self, seeded):
        async with session_scope() as session:
            events = await repo.get_events(session, start=REF, query="zzzyok", limit=20)
        assert events == []

    async def test_counts(self, seeded):
        async with session_scope() as session:
            counts = await repo.count_events(session, ref=REF)
        assert counts["gelecek"] == 4
        assert counts["ucretsiz"] == 2

    async def test_category_counts(self, seeded):
        async with session_scope() as session:
            counts = await repo.category_counts(session, ref=REF)
        assert counts.get("sergi") == 1


class TestNewEventFlow:
    async def test_new_events_then_marked(self, db, event_factory):
        event = event_factory("bubilet", "Yeni Konser", "12 Eylül 2026", "Kültürpark")
        async with session_scope() as session:
            await repo.upsert_events(session, [event])

        async with session_scope() as session:
            pending = await repo.get_new_events(session, ref=REF)
            assert len(pending) == 1
            await repo.mark_announced(session, [e.uid() for e in pending])

        async with session_scope() as session:
            assert await repo.get_new_events(session, ref=REF) == []


class TestPrune:
    async def test_old_events_removed(self, db, event_factory):
        old = event_factory(
            "bubilet", "Eski Etkinlik", "1 Ocak 2026", "Yer", ref_date=date(2026, 1, 1)
        )
        recent = event_factory("bubilet", "Yeni Etkinlik", "12 Eylül 2026", "Yer")
        async with session_scope() as session:
            await repo.upsert_events(session, [old, recent])

        async with session_scope() as session:
            removed = await repo.prune_old_events(session, keep_days=30, ref=REF)
        assert removed == 1

        async with session_scope() as session:
            counts = await repo.count_events(session, ref=REF)
        assert counts["toplam"] == 1


class TestSubscribers:
    async def test_create_and_toggle(self, db):
        async with session_scope() as session:
            sub = await repo.upsert_subscriber(session, 123, title="Test")
            assert sub.digest_enabled is True
            await repo.upsert_subscriber(session, 123, digest_enabled=False)

        async with session_scope() as session:
            sub = await repo.get_subscriber(session, 123)
        assert sub.digest_enabled is False

    async def test_list_filters(self, db):
        async with session_scope() as session:
            await repo.upsert_subscriber(session, 1, digest_enabled=True, notify_new=False)
            await repo.upsert_subscriber(session, 2, digest_enabled=False, notify_new=True)

        async with session_scope() as session:
            digest = await repo.list_subscribers(session, digest_only=True)
            notify = await repo.list_subscribers(session, notify_only=True)
        assert {s.chat_id for s in digest} == {1}
        assert {s.chat_id for s in notify} == {2}

    async def test_deactivate_excludes_from_lists(self, db):
        async with session_scope() as session:
            await repo.upsert_subscriber(session, 5)
            await repo.deactivate_subscriber(session, 5)

        async with session_scope() as session:
            assert await repo.list_subscribers(session) == []


class TestSourceHealth:
    async def test_failure_counter(self, db):
        async with session_scope() as session:
            await repo.update_source_health(
                session, "bubilet", count=0, strategy=None, error="HTTP 500"
            )
            await repo.update_source_health(
                session, "bubilet", count=0, strategy=None, error="HTTP 500"
            )

        async with session_scope() as session:
            rows = await repo.source_health(session)
        assert rows[0].consecutive_failures == 2
        assert rows[0].last_ok_at is None

    async def test_success_resets_counter(self, db):
        async with session_scope() as session:
            await repo.update_source_health(session, "bubilet", count=0, strategy=None, error="x")
            await repo.update_source_health(
                session, "bubilet", count=12, strategy="jsonld", error=None
            )

        async with session_scope() as session:
            rows = await repo.source_health(session)
        assert rows[0].consecutive_failures == 0
        assert rows[0].last_strategy == "jsonld"
        assert rows[0].last_ok_at is not None

    async def test_run_recorded(self, db):
        async with session_scope() as session:
            await repo.record_run(
                session,
                raw_count=100,
                unique_count=60,
                new_count=5,
                duration=12.5,
                per_source={"bubilet": 40},
                errors=[],
            )

        async with session_scope() as session:
            run = await repo.last_run(session)
        assert run.raw_count == 100
        assert run.unique_count == 60
        assert run.duration_seconds == pytest.approx(12.5)
