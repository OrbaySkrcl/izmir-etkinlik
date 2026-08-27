"""Çıkarım stratejileri testleri."""

import pytest

from izmir_events.config import Selectors, load_sources
from izmir_events.models import build_event
from izmir_events.scrape.extractors import (
    extract_heuristic,
    extract_jsonld,
    extract_nextdata,
    extract_selectors,
    parse_selector,
    self_matches,
)

BASE = "https://site.example/izmir"


@pytest.fixture
def kultursanat_selectors():
    return next(s for s in load_sources() if s.key == "kultursanat").selectors


class TestParseSelector:
    def test_attribute_suffix(self):
        assert parse_selector("a@href") == [("a", "href")]

    def test_multiple_candidates(self):
        assert parse_selector("h3, .title") == [("h3", None), (".title", None)]

    def test_mixed(self):
        assert parse_selector("img@data-src, img@src") == [("img", "data-src"), ("img", "src")]

    def test_empty(self):
        assert parse_selector("") == []


class TestSelfMatches:
    """Kart elemanının kendisi hedef eleman olduğunda seçici çalışmalı."""

    @pytest.fixture
    def nodes(self):
        from selectolax.parser import HTMLParser

        tree = HTMLParser(
            '<div class="etkinlik-item card">'
            '<a href="/etkinlik/5" id="ilk">Başlık</a>'
            '<span class="etkinlik-yer">Mekan</span></div>'
        )
        return tree.css_first("div"), tree.css_first("a")

    @pytest.mark.parametrize(
        ("which", "selector", "expected"),
        [
            ("div", ".etkinlik-item", True),
            ("div", ".etkinlik-yer", False),  # alt düğümde var ama kendisi değil
            ("div", "div", True),
            ("div", "span", False),
            ("div", ".card.etkinlik-item", True),
            ("div", "div .card", False),  # karmaşık seçici: eşleşme sayma
            ("a", "a", True),
            ("a", "[href]", True),
            ("a", "a[href*='/etkinlik/']", True),
            ("a", "a[href*='/bilet/']", False),
            ("a", "#ilk", True),
            ("a", "#baska", False),
        ],
    )
    def test_matching(self, nodes, which, selector, expected):
        div, anchor = nodes
        node = div if which == "div" else anchor
        assert self_matches(node, selector) is expected

    def test_item_selector_is_the_anchor_itself(self):
        """item seçicisi <a> ise, url seçicisi de o <a>'dan okunmalı."""
        html = (
            "<html><body>"
            "<a href='/etkinlik/1' class='kart'>Konser Bir<span>12 Eylül 2026</span></a>"
            "<a href='/etkinlik/2' class='kart'>Konser İki<span>13 Eylül 2026</span></a>"
            "</body></html>"
        )
        selectors = Selectors(item="a.kart", title="a@title, .baslik", url="a@href", date="span")
        events = extract_selectors(html, BASE, "test", selectors)
        assert len(events) == 2
        assert events[0].url == "https://site.example/etkinlik/1"


class TestJsonLd:
    @pytest.fixture
    def events(self, fixtures):
        html = (fixtures / "jsonld_listing.html").read_text(encoding="utf-8")
        return extract_jsonld(html, BASE, "test")

    def test_finds_all_event_types(self, events):
        # MusicEvent, TheaterEvent, ExhibitionEvent
        assert len(events) == 3
        assert {e.title for e in events} == {"Sezen Aksu Konseri", "Hamlet", "Modern Sanat Sergisi"}

    def test_relative_url_absolutized(self, events):
        concert = next(e for e in events if e.title == "Sezen Aksu Konseri")
        assert concert.url == "https://site.example/etkinlik/sezen-aksu-izmir"

    def test_nested_place_name_extracted(self, events):
        concert = next(e for e in events if e.title == "Sezen Aksu Konseri")
        assert concert.venue == "Kültürpark Açıkhava Tiyatrosu"

    def test_string_location_supported(self, events):
        play = next(e for e in events if e.title == "Hamlet")
        assert play.venue == "İzmir Sanat"

    def test_offer_price_range(self, events, ref):
        play = next(e for e in events if e.title == "Hamlet")
        event = build_event(play, ref=ref)
        assert event.price_min == 200.0
        assert event.price_max == 600.0

    def test_zero_price_means_free(self, events, ref):
        show = next(e for e in events if e.title == "Modern Sanat Sergisi")
        assert build_event(show, ref=ref).is_free

    def test_end_date_creates_range(self, events, ref):
        show = next(e for e in events if e.title == "Modern Sanat Sergisi")
        event = build_event(show, ref=ref)
        assert event.end > event.start
        assert event.dates.multi_day

    def test_no_jsonld_returns_empty(self):
        assert extract_jsonld("<html><body>hiçbir şey</body></html>", BASE, "test") == []

    def test_malformed_json_does_not_raise(self):
        html = '<script type="application/ld+json">{"@type":"Event" bozuk}</script>'
        assert extract_jsonld(html, BASE, "test") == []


class TestNextData:
    @pytest.fixture
    def events(self, fixtures):
        html = (fixtures / "nextdata_listing.html").read_text(encoding="utf-8")
        return extract_nextdata(html, BASE, "test")

    def test_extracts_events_only(self, events):
        # Üçüncü kayıt bir kullanıcı yorumu; etkinlik sayılmamalı.
        assert len(events) == 2
        assert "Kullanıcı Yorumu" not in {e.title for e in events}

    def test_numeric_price_gets_currency(self, events, ref):
        concert = next(e for e in events if e.title == "Sezen Aksu")
        assert build_event(concert, ref=ref).price_min == 450.0

    def test_alternate_key_names(self, events):
        # "title"/"path"/"place" gibi farklı anahtar adları da okunmalı.
        show = next(e for e in events if "Cem Yılmaz" in e.title)
        assert show.venue == "Fuar İzmir"
        assert show.url == "https://site.example/izmir/cem-yilmaz"

    def test_no_script_returns_empty(self):
        assert extract_nextdata("<html></html>", BASE, "test") == []


class TestSelectors:
    @pytest.fixture
    def events(self, fixtures, kultursanat_selectors):
        html = (fixtures / "selectors_listing.html").read_text(encoding="utf-8")
        return extract_selectors(html, BASE, "test", kultursanat_selectors)

    def test_extracts_all_cards(self, events):
        assert len(events) == 3

    def test_fields_mapped(self, events):
        first = events[0]
        assert "Ezginin Günlüğü" in first.title
        assert first.venue == "Kültürpark Açıkhava Tiyatrosu"
        assert first.url == "https://site.example/Etkinlik/Detay/551"

    def test_data_src_image_read(self, events):
        assert events[0].image == "https://site.example/img/1.jpg"

    def test_free_price_detected(self, events, ref):
        kids = next(e for e in events if "Uçan Balon" in e.title)
        assert build_event(kids, ref=ref).is_free

    def test_date_range_card(self, events, ref):
        exhibition = next(e for e in events if "Sergisi" in e.title)
        event = build_event(exhibition, ref=ref)
        assert event.dates.multi_day

    def test_missing_item_selector_returns_empty(self, fixtures):
        html = (fixtures / "selectors_listing.html").read_text(encoding="utf-8")
        assert extract_selectors(html, BASE, "test", Selectors()) == []

    def test_non_matching_selector_returns_empty(self, fixtures):
        html = (fixtures / "selectors_listing.html").read_text(encoding="utf-8")
        selectors = Selectors(item=".boyle-bir-sinif-yok", title="h3")
        assert extract_selectors(html, BASE, "test", selectors) == []


class TestHeuristic:
    @pytest.fixture
    def events(self, fixtures):
        html = (fixtures / "heuristic_listing.html").read_text(encoding="utf-8")
        return extract_heuristic(html, BASE, "test")

    def test_finds_repeating_cards(self, events):
        assert len(events) == 3

    def test_ignores_navigation_and_footer(self, events):
        titles = {e.title for e in events}
        assert "Anasayfa" not in titles
        assert not any("2026 site" in t for t in titles)

    def test_titles_and_links(self, events):
        assert "Manuş Baba Konseri" in {e.title for e in events}
        assert all(e.url and e.url.startswith("https://site.example/e/") for e in events)

    def test_prices_not_confused_with_dates(self, events, ref):
        # Kart metninde hem "18 Eylül" hem "650 TL" var.
        concert = next(e for e in events if "Manuş Baba" in e.title)
        assert build_event(concert, ref=ref).price_min == 650.0

    def test_free_card_detected(self, events, ref):
        reading = next(e for e in events if "Şiir" in e.title)
        assert build_event(reading, ref=ref).is_free

    def test_page_without_dates_returns_empty(self):
        html = "<html><body><div class='card'><a href='/x'>Bir şey</a></div></body></html>"
        assert extract_heuristic(html, BASE, "test") == []
