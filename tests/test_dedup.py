"""Tekilleştirme testleri.

Kullanıcının asıl derdi burada: aynı etkinlik farklı kaynaklarda farklı
yazılmış olabiliyor.
"""

import pytest

from izmir_events.dedup.cluster import UnionFind, deduplicate, merge_cluster
from izmir_events.dedup.similarity import compare, title_similarity

PRIORITY = {
    "kultursanat": 90,
    "biletix": 75,
    "bubilet": 70,
    "biletinial": 70,
    "oggusto": 40,
    "izmirmag": 30,
}


class TestUnionFind:
    def test_groups_transitively(self):
        uf = UnionFind(5)
        uf.union(0, 1)
        uf.union(1, 2)
        uf.union(3, 4)
        groups = sorted(sorted(g) for g in uf.groups().values())
        assert groups == [[0, 1, 2], [3, 4]]

    def test_singletons(self):
        assert len(UnionFind(3).groups()) == 3


class TestTitleSimilarity:
    def test_identical(self):
        assert title_similarity("sezen aksu", "sezen aksu") == 1.0

    def test_subset_penalised(self):
        # "hamlet" ⊂ "hamlet makinesi" olsa da tam puan almamalı.
        assert title_similarity("hamlet", "hamlet makinesi") < 0.9

    def test_empty(self):
        assert title_similarity("", "sezen aksu") == 0.0


class TestCompare:
    def test_same_event_different_wording(self, event_factory):
        a = event_factory("bubilet", "Sezen Aksu", "12 Eylül 2026", "Kültürpark Açıkhava Tiyatrosu")
        b = event_factory("oggusto", "Sezen Aksu Konseri", "12 Eylül 2026", "Kültürpark")
        assert compare(a, b).score >= 0.82

    def test_promotional_headline_matches(self, event_factory):
        a = event_factory("bubilet", "Sezen Aksu", "12 Eylül 2026", "Kültürpark")
        b = event_factory(
            "izmirmag", "Efsane Sanatçı Sezen Aksu İzmir'de!", "12 Eylül 2026", "Kültürpark"
        )
        assert compare(a, b).score >= 0.82

    def test_different_works_same_venue_not_merged(self, event_factory):
        a = event_factory("bubilet", "Hamlet", "12 Eylül 2026", "İzmir Sanat")
        b = event_factory("biletinial", "Hamlet Makinesi", "12 Eylül 2026", "İzmir Sanat")
        assert compare(a, b).score < 0.82

    def test_same_title_different_venue_not_merged(self, event_factory):
        # Aynı oyun aynı gece iki ayrı sahnede oynanabiliyor.
        a = event_factory("bubilet", "Romeo ve Juliet", "12 Eylül 2026", "İzmir Sanat")
        b = event_factory(
            "biletinial", "Romeo ve Juliet", "12 Eylül 2026", "Ahmed Adnan Saygun Sanat Merkezi"
        )
        assert compare(a, b).score < 0.82

    def test_different_days_never_merge(self, event_factory):
        a = event_factory("bubilet", "Sezen Aksu", "12 Eylül 2026", "Kültürpark")
        b = event_factory("bubilet", "Sezen Aksu", "20 Eylül 2026", "Kültürpark")
        assert compare(a, b).score == 0.0

    def test_adjacent_days_merge_with_tolerance(self, event_factory):
        # Kaynaklar bazen bir gün kaydırıyor (gece yarısını aşan etkinlikler).
        a = event_factory("bubilet", "Gece Festivali", "12 Eylül 2026", "Kültürpark")
        b = event_factory("oggusto", "Gece Festivali", "13 Eylül 2026", "Kültürpark")
        assert compare(a, b, date_tolerance_days=1).score >= 0.82
        assert compare(a, b, date_tolerance_days=0).score == 0.0

    def test_identical_url_is_definitive(self, event_factory):
        a = event_factory("bubilet", "Bir Etkinlik", "12 Eylül 2026", url="https://x.test/e/1")
        b = event_factory(
            "oggusto", "Tamamen Başka İsim", "12 Eylül 2026", url="https://x.test/e/1"
        )
        assert compare(a, b).score == 1.0

    def test_missing_venue_still_matches_on_title(self, event_factory):
        a = event_factory("bubilet", "Sezen Aksu", "12 Eylül 2026", None)
        b = event_factory("oggusto", "Sezen Aksu Konseri", "12 Eylül 2026", None)
        assert compare(a, b).score >= 0.82


class TestDeduplicate:
    def test_five_variants_become_one(self, event_factory):
        events = [
            event_factory(
                "bubilet", "Sezen Aksu", "12 Eylül 2026", "Kültürpark Açıkhava Tiyatrosu", "450 TL"
            ),
            event_factory(
                "biletinial",
                "Sezen Aksu Konseri",
                "12.09.2026",
                "Kültürpark Açıkhava",
                "450 TL - 1200 TL",
            ),
            event_factory(
                "oggusto",
                "Sezen Aksu - Kültürpark Açıkhava Tiyatrosu",
                "12 Eylül 2026",
                "Kültürpark",
            ),
            event_factory(
                "izmirmag", "Efsane Sanatçı Sezen Aksu İzmir'de!", "12 Eylül 2026", "Kültürpark"
            ),
            event_factory(
                "biletix",
                "SEZEN AKSU | İZMİR",
                "2026-09-12",
                "Kültürpark Açıkhava Tiyatrosu",
                "500 TL",
            ),
        ]
        merged, stats = deduplicate(events, source_priority=PRIORITY)
        assert len(merged) == 1
        assert merged[0].source_count == 5
        assert stats.removed == 4

    def test_merged_event_keeps_widest_price_range(self, event_factory):
        events = [
            event_factory("bubilet", "Sezen Aksu", "12 Eylül 2026", "Kültürpark", "450 TL"),
            event_factory(
                "biletix", "Sezen Aksu Konseri", "12 Eylül 2026", "Kültürpark", "1200 TL"
            ),
        ]
        merged, _ = deduplicate(events, source_priority=PRIORITY)
        assert merged[0].price_min == 450.0
        assert merged[0].price_max == 1200.0

    def test_canonical_title_is_readable(self, event_factory):
        events = [
            event_factory("biletix", "SEZEN AKSU | İZMİR", "12 Eylül 2026", "Kültürpark"),
            event_factory("bubilet", "Sezen Aksu", "12 Eylül 2026", "Kültürpark"),
        ]
        merged, _ = deduplicate(events, source_priority=PRIORITY)
        assert merged[0].title == "Sezen Aksu"

    def test_shouting_title_cleaned_even_when_alone(self, event_factory):
        events = [event_factory("biletix", "SEZEN AKSU", "12 Eylül 2026", "Kültürpark")]
        merged, _ = deduplicate(events, source_priority=PRIORITY)
        assert merged[0].title == "Sezen Aksu"

    def test_higher_priority_source_provides_venue(self, event_factory):
        events = [
            event_factory("izmirmag", "Bir Konser", "12 Eylül 2026", None),
            event_factory("kultursanat", "Bir Konser", "12 Eylül 2026", "İzmir Sanat"),
        ]
        merged, _ = deduplicate(events, source_priority=PRIORITY)
        assert merged[0].venue == "İzmir Sanat"

    def test_multiday_ranges_widen(self, event_factory):
        events = [
            event_factory(
                "kultursanat",
                "Modern Sanat Sergisi",
                "1 Eylül - 30 Ekim 2026",
                "Arkas Sanat Merkezi",
            ),
            event_factory(
                "oggusto", "Modern Sanat Sergisi", "1 Eylül - 15 Kasım 2026", "Arkas Sanat Merkezi"
            ),
        ]
        merged, _ = deduplicate(events, source_priority=PRIORITY)
        assert len(merged) == 1
        assert merged[0].end.month == 11

    def test_distinct_events_preserved(self, event_factory):
        events = [
            event_factory("bubilet", "Hamlet", "20 Eylül 2026", "İzmir Sanat"),
            event_factory("biletinial", "Hamlet Makinesi", "20 Eylül 2026", "İzmir Sanat"),
            event_factory("bubilet", "Manuş Baba", "20 Eylül 2026", "Arena İzmir"),
        ]
        merged, _ = deduplicate(events, source_priority=PRIORITY)
        assert len(merged) == 3

    def test_free_flag_survives_merge(self, event_factory):
        events = [
            event_factory(
                "kultursanat",
                "Çocuk Tiyatrosu",
                "14 Eylül 2026",
                "İzmir Sanat",
                free_by_default=True,
            ),
            event_factory("izmirmag", "Çocuk Tiyatrosu Oyunu", "14 Eylül 2026", "İzmir Sanat"),
        ]
        merged, _ = deduplicate(events, source_priority=PRIORITY)
        assert len(merged) == 1
        assert merged[0].is_free

    def test_results_sorted_by_date(self, event_factory):
        events = [
            event_factory("bubilet", "Geç Etkinlik", "20 Eylül 2026"),
            event_factory("bubilet", "Erken Etkinlik", "12 Eylül 2026"),
        ]
        merged, _ = deduplicate(events, source_priority=PRIORITY)
        assert [e.title for e in merged] == ["Erken Etkinlik", "Geç Etkinlik"]

    def test_source_titles_recorded_for_transparency(self, event_factory):
        events = [
            event_factory("bubilet", "Sezen Aksu", "12 Eylül 2026", "Kültürpark"),
            event_factory("oggusto", "Sezen Aksu Konseri", "12 Eylül 2026", "Kültürpark"),
        ]
        merged, _ = deduplicate(events, source_priority=PRIORITY)
        assert set(merged[0].source_titles.values()) == {"Sezen Aksu", "Sezen Aksu Konseri"}

    @pytest.mark.parametrize("count", [0, 1])
    def test_trivial_inputs(self, event_factory, count):
        events = [event_factory("bubilet", "Tek", "12 Eylül 2026")] * count
        merged, stats = deduplicate(events)
        assert len(merged) == count
        assert stats.input_count == count

    def test_blocking_limits_comparisons(self, event_factory):
        # 60 etkinlik, 30 ayrı güne yayılmış: kaba kuvvet 1770 karşılaştırma
        # yapardı, bloklama bunu çok daha aza indirmeli.
        events = [
            event_factory("bubilet", f"Etkinlik {i}", f"{(i % 28) + 1} Eylül 2026")
            for i in range(60)
        ]
        _, stats = deduplicate(events)
        assert stats.comparisons < 200


class TestMergeCluster:
    def test_single_member_returns_cleaned_copy(self, event_factory):
        event = event_factory("bubilet", "TEK ETKİNLİK", "12 Eylül 2026")
        merged = merge_cluster([event], PRIORITY)
        assert merged.title == "Tek Etkinlik"

    def test_category_upgraded_from_other(self, event_factory):
        vague = event_factory("bubilet", "Bir Şey", "12 Eylül 2026", "Bilinmeyen Yer")
        specific = event_factory("oggusto", "Bir Şey Konseri", "12 Eylül 2026", "Bilinmeyen Yer")
        merged = merge_cluster([vague, specific], PRIORITY)
        assert merged.category.value == "konser"
