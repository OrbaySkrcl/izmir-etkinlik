"""Fiyat ayrıştırma testleri."""

import pytest

from izmir_events.util.money import Price, parse_price


class TestFreeDetection:
    @pytest.mark.parametrize(
        "raw",
        ["Ücretsiz", "ÜCRETSİZDİR", "Giriş Serbest", "ücretsizdir", "Katılım ücretsiz", "Free"],
    )
    def test_free_phrases(self, raw):
        assert parse_price(raw).is_free

    def test_free_with_registration(self):
        price = parse_price("Ücretsiz (davetiyelidir)")
        assert price.is_free
        assert price.needs_registration
        assert "kayıt gerekli" in price.label()

    def test_free_by_default_when_no_info(self):
        assert parse_price(None, free_by_default=True).is_free
        assert not parse_price(None).is_free

    def test_explicit_price_overrides_free_default(self):
        price = parse_price("100 TL", free_by_default=True)
        assert not price.is_free
        assert price.min_amount == 100


class TestAmounts:
    @pytest.mark.parametrize(
        ("raw", "expected_min", "expected_max"),
        [
            ("150,00 TL", 150.0, None),
            ("₺150", 150.0, None),
            ("1.250,50 TL", 1250.5, None),
            ("150 TL - 400 TL", 150.0, 400.0),
            ("450₺'den başlayan fiyatlarla", 450.0, None),
            ("Fiyat: 200", 200.0, None),
        ],
    )
    def test_parsing(self, raw, expected_min, expected_max):
        price = parse_price(raw)
        assert price.min_amount == expected_min
        assert price.max_amount == expected_max

    def test_date_digits_not_read_as_price(self):
        # Kart metninin tamamı verildiğinde tarihteki "18" fiyat sanılmamalı.
        price = parse_price("Manuş Baba Konseri 18 Eylül 2026 Arena İzmir 650 TL")
        assert price.min_amount == 650.0
        assert price.max_amount is None

    def test_year_not_read_as_price(self):
        assert parse_price("2026 sezonu").min_amount is None

    def test_unknown_when_no_signal(self):
        assert parse_price("Bilet Al").unknown


class TestLabels:
    def test_free_label(self):
        assert parse_price("Ücretsiz").label() == "Ücretsiz"

    def test_range_label(self):
        assert parse_price("150 TL - 400 TL").label() == "150–400 ₺"

    def test_thousands_formatted_turkish(self):
        assert parse_price("1250 TL").label() == "1.250 ₺"

    def test_decimal_formatted_turkish(self):
        assert parse_price("1.250,50 TL").label() == "1.250,50 ₺"

    def test_unknown_label(self):
        assert parse_price(None).label() == "Fiyat belirtilmemiş"


class TestMerge:
    def test_known_beats_unknown(self):
        known = parse_price("150 TL")
        assert Price().merge(known) == known
        assert known.merge(Price()) == known

    def test_range_widens(self):
        merged = parse_price("150 TL").merge(parse_price("400 TL"))
        assert merged.min_amount == 150.0
        assert merged.max_amount == 400.0

    def test_free_and_free_stays_free(self):
        assert parse_price("Ücretsiz").merge(parse_price("Giriş serbest")).is_free

    def test_priced_source_wins_over_free_claim(self):
        # Bir kaynak "ücretsiz" derken diğeri fiyat veriyorsa fiyat daha spesifiktir.
        merged = parse_price("Ücretsiz").merge(parse_price("150 TL"))
        assert not merged.is_free
        assert merged.min_amount == 150.0
