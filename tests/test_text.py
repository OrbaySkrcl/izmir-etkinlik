"""Türkçe metin normalizasyonu testleri."""

import pytest

from izmir_events.util.text import (
    clean_display_title,
    core_title,
    is_shouting,
    normalize_title,
    normalize_venue,
    slugify,
    tr_lower,
    tr_title,
    tr_upper,
    truncate,
)


class TestTurkishCase:
    def test_lower_handles_dotted_and_dotless_i(self):
        # Python'ın str.lower()'ı bunları yanlış yapar.
        assert tr_lower("IĞDIR") == "ığdır"
        assert tr_lower("İZMİR") == "izmir"
        assert tr_lower("İstanbul") == "istanbul"

    def test_upper_handles_i(self):
        assert tr_upper("izmir") == "İZMİR"
        assert tr_upper("ışık") == "IŞIK"

    def test_title(self):
        assert tr_title("izmir kültür sanat") == "İzmir Kültür Sanat"


class TestNormalizeTitle:
    @pytest.mark.parametrize(
        "raw",
        [
            "Sezen Aksu Konseri",
            "SEZEN AKSU | İzmir",
            "Sezen Aksu - Kültürpark Açıkhava Tiyatrosu",
            "Sezen Aksu (İzmir) Bileti 2026",
            "sezen aksu konser bileti",
            "Efsane Sanatçı Sezen Aksu İzmir'de!",
        ],
    )
    def test_variants_collapse_to_same_key(self, raw):
        assert normalize_title(raw) == "sezen aksu"

    def test_distinct_works_stay_distinct(self):
        assert normalize_title("Hamlet") != normalize_title("Hamlet Makinesi")

    def test_never_returns_empty_for_all_noise_title(self):
        # Başlığın tamamı gürültü sözcüğüyse bile boş anahtar üretme.
        assert normalize_title("Konser Bileti") != ""

    def test_accent_insensitive(self):
        assert normalize_title("Şarkılar Gecesi") == normalize_title("Sarkilar Gecesi")


class TestCoreTitle:
    def test_strips_venue_after_separator(self):
        assert core_title("Sezen Aksu - Kültürpark") == "Sezen Aksu"

    def test_keeps_whole_when_prefix_too_short(self):
        assert core_title("İzmir | Sezen Aksu") == "Sezen Aksu"

    def test_no_separator_returns_input(self):
        assert core_title("Sezen Aksu") == "Sezen Aksu"


class TestVenue:
    def test_aliases_unify(self):
        assert normalize_venue("Kültürpark Açıkhava Tiyatrosu") == normalize_venue("Kültürpark")

    def test_generic_suffixes_dropped(self):
        assert normalize_venue("Arkas Sanat Merkezi") == normalize_venue("Arkas")

    def test_empty(self):
        assert normalize_venue(None) == ""
        assert normalize_venue("") == ""


class TestDisplayTitle:
    def test_shouting_converted_to_title_case(self):
        assert clean_display_title("SEZEN AKSU") == "Sezen Aksu"

    def test_trailing_city_removed(self):
        assert clean_display_title("Sezen Aksu | İZMİR") == "Sezen Aksu"

    def test_normal_title_untouched(self):
        assert clean_display_title("Çocuk Tiyatrosu: Uçan Balon") == "Çocuk Tiyatrosu: Uçan Balon"

    def test_is_shouting(self):
        assert is_shouting("SEZEN AKSU")
        assert not is_shouting("Sezen Aksu")
        assert not is_shouting("AB")  # çok kısa


class TestMisc:
    def test_slugify(self):
        assert slugify("Sezen Aksu Konseri!") == "sezen-aksu-konseri"

    def test_truncate_respects_word_boundary(self):
        assert truncate("bir iki üç dört", 10) == "bir iki…"

    def test_truncate_noop_when_short(self):
        assert truncate("kısa", 10) == "kısa"
