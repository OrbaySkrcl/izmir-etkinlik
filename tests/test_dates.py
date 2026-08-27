"""Türkçe tarih ayrıştırma ve kovalama testleri."""

from datetime import date, time

import pytest

from izmir_events.util.dates import (
    Bucket,
    EventDates,
    bucket_bounds,
    classify,
    format_dates,
    parse_dates,
    parse_time,
    week_bounds,
    weekend_bounds,
)

REF = date(2026, 8, 27)  # Perşembe


class TestParseDates:
    @pytest.mark.parametrize(
        ("raw", "expected_start", "expected_end"),
        [
            ("12 Eylül 2026", date(2026, 9, 12), date(2026, 9, 12)),
            ("12 Eyl", date(2026, 9, 12), date(2026, 9, 12)),
            ("12.09.2026", date(2026, 9, 12), date(2026, 9, 12)),
            ("12/09/2026", date(2026, 9, 12), date(2026, 9, 12)),
            ("2026-09-12", date(2026, 9, 12), date(2026, 9, 12)),
            ("2026-09-12T21:00:00+03:00", date(2026, 9, 12), date(2026, 9, 12)),
            ("12 - 15 Eylül 2026", date(2026, 9, 12), date(2026, 9, 15)),
            ("12 Eylül - 3 Ekim 2026", date(2026, 9, 12), date(2026, 10, 3)),
            ("1 Ağustos - 30 Ekim 2026", date(2026, 8, 1), date(2026, 10, 30)),
        ],
    )
    def test_absolute_dates(self, raw, expected_start, expected_end):
        parsed = parse_dates(raw, ref=REF)
        assert parsed is not None
        assert (parsed.start, parsed.end) == (expected_start, expected_end)

    def test_relative_today_and_tomorrow(self):
        assert parse_dates("Bugün", ref=REF).start == REF
        assert parse_dates("Yarın 20:00", ref=REF).start == date(2026, 8, 28)

    def test_weekday_resolves_to_next_occurrence(self):
        # 27 Ağustos Perşembe -> "Cumartesi" = 29 Ağustos
        assert parse_dates("Cumartesi 21.00", ref=REF).start == date(2026, 8, 29)

    def test_year_inferred_forward_across_new_year(self):
        # Aralık'ta görülen "5 Ocak" gelecek yılı işaret eder.
        december = date(2026, 12, 20)
        assert parse_dates("5 Ocak", ref=december).start == date(2027, 1, 5)

    def test_cross_year_range(self):
        parsed = parse_dates("28 Aralık - 5 Ocak", ref=REF)
        assert parsed.start == date(2026, 12, 28)
        assert parsed.end == date(2027, 1, 5)

    def test_recent_past_not_pushed_to_next_year(self):
        # 5 gün önce biten etkinlik 1 yıl ileri atılmamalı.
        assert parse_dates("22 Ağustos", ref=REF).start == date(2026, 8, 22)

    def test_time_extracted(self):
        parsed = parse_dates("12 Eylül 2026 Saat: 20.30", ref=REF)
        assert parsed.start_time == time(20, 30)

    def test_numeric_date_not_mistaken_for_time(self):
        # "12.09" saat gibi görünür ama tarihtir.
        parsed = parse_dates("12.09.2026 21:00", ref=REF)
        assert parsed.start_time == time(21, 0)

    def test_date_without_time_has_none(self):
        assert parse_dates("12.09.2026", ref=REF).start_time is None

    @pytest.mark.parametrize("raw", [None, "", "tarih yok", "Bilet Al", "  "])
    def test_unparseable_returns_none(self, raw):
        assert parse_dates(raw, ref=REF) is None

    def test_invalid_calendar_date_rejected(self):
        assert parse_dates("32 Eylül 2026", ref=REF) is None


class TestParseTime:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("21:00", time(21, 0)), ("21.00", time(21, 0)), ("09:05", time(9, 5))],
    )
    def test_valid(self, raw, expected):
        assert parse_time(raw) == expected

    def test_invalid_hour_ignored(self):
        assert parse_time("25:00") is None


class TestBuckets:
    def test_week_bounds_monday_to_sunday(self):
        start, end = week_bounds(REF)
        assert start == date(2026, 8, 24)  # Pazartesi
        assert end == date(2026, 8, 30)  # Pazar

    def test_weekend_bounds(self):
        sat, sun = weekend_bounds(REF)
        assert (sat, sun) == (date(2026, 8, 29), date(2026, 8, 30))

    @pytest.mark.parametrize(
        ("start", "expected"),
        [
            (REF, Bucket.TODAY),
            (date(2026, 8, 28), Bucket.TOMORROW),
            (date(2026, 8, 30), Bucket.THIS_WEEK),
            (date(2026, 9, 2), Bucket.NEXT_WEEK),
            (date(2026, 9, 20), Bucket.LATER),
            (date(2026, 11, 1), Bucket.LATER),
            (date(2026, 8, 1), Bucket.PAST),
        ],
    )
    def test_classify(self, start, expected):
        assert classify(EventDates(start, start), REF) is expected

    def test_this_month_reachable_mid_month(self):
        # Ayın başında, gelecek haftadan sonraki ama ay içindeki günler BU_AY olur.
        ref = date(2026, 9, 1)
        assert classify(EventDates(date(2026, 9, 25), date(2026, 9, 25)), ref) is Bucket.THIS_MONTH

    def test_ongoing_multiday_event_counts_as_today(self):
        ongoing = EventDates(date(2026, 8, 1), date(2026, 10, 30), is_range=True)
        assert classify(ongoing, REF) is Bucket.TODAY

    def test_bucket_bounds_today(self):
        assert bucket_bounds(Bucket.TODAY, REF) == (REF, REF)

    def test_bucket_bounds_next_week(self):
        start, end = bucket_bounds(Bucket.NEXT_WEEK, REF)
        assert start == date(2026, 8, 31)
        assert end == date(2026, 9, 6)


class TestOverlap:
    def test_overlapping_ranges(self):
        a = EventDates(date(2026, 9, 1), date(2026, 9, 10), is_range=True)
        b = EventDates(date(2026, 9, 5), date(2026, 9, 15), is_range=True)
        assert a.overlaps(b)

    def test_adjacent_days_with_tolerance(self):
        a = EventDates(date(2026, 9, 1), date(2026, 9, 1))
        b = EventDates(date(2026, 9, 2), date(2026, 9, 2))
        assert not a.overlaps(b, tolerance_days=0)
        assert a.overlaps(b, tolerance_days=1)


class TestFormat:
    def test_today_prefix(self):
        assert format_dates(EventDates(REF, REF), ref=REF).startswith("Bugün")

    def test_tomorrow_prefix(self):
        tomorrow = date(2026, 8, 28)
        assert format_dates(EventDates(tomorrow, tomorrow), ref=REF).startswith("Yarın")

    def test_same_month_range_compact(self):
        text = format_dates(
            EventDates(date(2026, 9, 12), date(2026, 9, 15), is_range=True), ref=REF
        )
        assert text == "12-15 Eylül"

    def test_ongoing_marked(self):
        text = format_dates(
            EventDates(date(2026, 8, 1), date(2026, 10, 30), is_range=True), ref=REF
        )
        assert "devam ediyor" in text

    def test_future_year_shown(self):
        text = format_dates(EventDates(date(2027, 1, 5), date(2027, 1, 5)), ref=REF)
        assert "2027" in text
