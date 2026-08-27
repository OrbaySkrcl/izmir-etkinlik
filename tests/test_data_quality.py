"""Canlı botta görülen veri kalitesi sorunlarının regresyon testleri.

Telegram ekran görüntülerinde dört sorun tespit edildi:
  1. "29 Ağustos 2026" bir etkinlik *başlığı* olarak listeleniyordu
  2. Tarih başlığın içinde kalıyordu ("... Tiyatrosu 18 Eylül 2026")
  3. Hiçbir etkinlikte mekan (📍) görünmüyordu — mekan başlığın içindeydi
  4. Her etkinlik "Fiyat belirtilmemiş" gösteriyordu
"""

from datetime import date

import httpx
import pytest
import respx

from izmir_events.config import Selectors, SourceConfig
from izmir_events.models import RawEvent, build_event
from izmir_events.render import event_line
from izmir_events.scrape.extractors import extract_heuristic, extract_selectors, pick_title
from izmir_events.scrape.http import HttpClient
from izmir_events.scrape.runner import _is_junk, scrape_source
from izmir_events.util.dates import is_date_only, strip_date_expressions
from izmir_events.util.text import split_venue_from_title

REF = date(2026, 8, 27)
BASE = "https://site.example/izmir"


class TestDateOnlyTitles:
    """Sorun 1: tarih elemanı başlık sanılıyordu."""

    @pytest.mark.parametrize(
        "text",
        [
            "29 Ağustos 2026",
            "30 Ağustos 2026",
            "29 Ağustos Cumartesi",
            "30 Ağustos Pazar",
            "12.09.2026",
            "12.09.2026 21:00",
            "21:00",
            "Bugün",
            "Yarın",
            "2026-09-12",
        ],
    )
    def test_recognised_as_date_only(self, text):
        assert is_date_only(text)
        assert _is_junk(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Bu Hikaye Senden Uzun Osman",
            "Pazar Yeri Festivali",  # "Pazar" gün adı ama başlığın parçası
            "Cuma Konserleri",
            "Sezen Aksu",
            "29 Mayıs Fetih Şenliği",  # tarih içeriyor ama etkinlik adı da var
        ],
    )
    def test_real_titles_not_dropped(self, text):
        assert not is_date_only(text)
        assert not _is_junk(text)

    def test_card_with_date_link_uses_real_title(self):
        """Link metni tarihse başlık bir sonraki adaydan alınmalı."""
        html = """<html><body>
            <div class="card">
              <a href="/e/1">29 Ağustos 2026</a>
              <h3>Bu Hikaye Senden Uzun Osman</h3>
              <span class="tarih">29 Ağustos 2026</span>
            </div></body></html>"""
        selectors = Selectors(item=".card", title="a", url="a@href", date=".tarih")
        events = extract_selectors(html, BASE, "test", selectors)
        assert len(events) == 1
        assert events[0].title == "Bu Hikaye Senden Uzun Osman"

    def test_pick_title_skips_all_date_candidates(self):
        from selectolax.parser import HTMLParser

        node = HTMLParser(
            "<div><a href='/e/1'>29 Ağustos 2026</a><span>21:00</span></div>"
        ).css_first("div")
        assert pick_title(node) is None


class TestDateInTitle:
    """Sorun 2: tarih başlıkta kalıyordu."""

    def test_trailing_date_stripped(self):
        title = "Konken Partisi İzmir / Bostanlı Suat Taşer Tiyatrosu 18 Eylül 2026"
        assert strip_date_expressions(title) == (
            "Konken Partisi İzmir / Bostanlı Suat Taşer Tiyatrosu"
        )

    def test_build_event_cleans_title(self):
        event = build_event(
            RawEvent(
                source="biletinial",
                title="Konken Partisi İzmir / Bostanlı Suat Taşer Tiyatrosu 18 Eylül 2026",
            ),
            ref=REF,
        )
        assert event is not None
        assert "2026" not in event.title
        assert "Eylül" not in event.title

    def test_date_only_in_title_still_parsed(self):
        """Tarih yalnızca başlıktaysa kayıt elenmemeli."""
        event = build_event(RawEvent(source="t", title="Konken Partisi 18 Eylül 2026"), ref=REF)
        assert event is not None
        assert event.start == date(2026, 9, 18)

    def test_title_that_is_only_a_date_keeps_original(self):
        """Ayıklama sonrası hiçbir şey kalmıyorsa orijinal korunur (junk filtresi eler)."""
        event = build_event(RawEvent(source="t", title="29 Ağustos 2026"), ref=REF)
        assert event is not None
        assert event.title == "29 Ağustos 2026"
        assert _is_junk(event.title)  # runner bunu eleyecek


class TestVenueInTitle:
    """Sorun 3: mekan başlığın içinde kalıyordu."""

    @pytest.mark.parametrize(
        ("title", "expected_title", "expected_venue"),
        [
            (
                "Konken Partisi İzmir / Bostanlı Suat Taşer Tiyatrosu",
                "Konken Partisi İzmir",
                "Bostanlı Suat Taşer Tiyatrosu",
            ),
            ("Hamlet - Devlet Tiyatrosu", "Hamlet", "Devlet Tiyatrosu"),
            (
                "Sezen Aksu | Kültürpark Açıkhava Tiyatrosu",
                "Sezen Aksu",
                "Kültürpark Açıkhava Tiyatrosu",
            ),
        ],
    )
    def test_venue_extracted(self, title, expected_title, expected_venue):
        assert split_venue_from_title(title) == (expected_title, expected_venue)

    @pytest.mark.parametrize(
        "title",
        [
            "Tiyatro Oyunu - Hamlet",  # mekan ayraçtan sonra değil
            "Bostanlı Suat Taşer Tiyatrosu",  # geriye başlık kalmaz
            "M.Tevfik Urgancıoğlu- Bir Adamla İlişkiler – İnteraktif Oyun Gecesi",
            "Bu Hikaye Senden Uzun Osman",
        ],
    )
    def test_not_split_when_unsafe(self, title):
        assert split_venue_from_title(title)[1] is None

    def test_explicit_venue_wins_over_title_split(self):
        event = build_event(
            RawEvent(
                source="t",
                title="Hamlet - Devlet Tiyatrosu",
                date_text="20 Eylül 2026",
                venue="İzmir Sanat",
            ),
            ref=REF,
        )
        assert event.venue == "İzmir Sanat"
        assert event.title == "Hamlet - Devlet Tiyatrosu"

    def test_venue_appears_in_telegram_output(self):
        event = build_event(
            RawEvent(
                source="biletinial",
                title="Konken Partisi / Bostanlı Suat Taşer Tiyatrosu 18 Eylül 2026",
                url="https://x.test/1",
            ),
            ref=REF,
        )
        assert "📍" in event_line(event, ref=REF)

    def test_category_uses_cleaned_title_not_venue_word(self):
        """Mekandaki "Tiyatrosu" kelimesi konseri tiyatro yapmamalı."""
        event = build_event(
            RawEvent(
                source="t",
                title="Sezen Aksu | Kültürpark Açıkhava Tiyatrosu",
                date_text="12 Eylül 2026",
            ),
            ref=REF,
        )
        assert event.category.value == "konser"


class TestVenueFallback:
    """Mekan seçicisi tutmadığında kart metninden okunabilmeli."""

    def _extract(self, html: str):
        selectors = Selectors(item=".card", title="h3", url="a@href", date=".tarih")
        return extract_selectors(html, BASE, "test", selectors)

    def test_venue_read_from_card_text(self):
        html = """<html><body><div class="card">
            <a href="/e/1"></a><h3>Bir Oyun</h3>
            <span class="tarih">18 Eylül 2026</span>
            <div class="info">Konak Sahnesi · 400 TL</div>
            </div></body></html>"""
        assert self._extract(html)[0].venue == "Konak Sahnesi"

    def test_price_chunk_not_taken_as_venue(self):
        html = """<html><body><div class="card">
            <a href="/e/1"></a><h3>Bir Oyun</h3>
            <span class="tarih">18 Eylül 2026</span>
            <div class="info">Kültürpark Açıkhava Tiyatrosu · 1.250 TL</div>
            </div></body></html>"""
        venue = self._extract(html)[0].venue
        assert venue == "Kültürpark Açıkhava Tiyatrosu"
        assert "TL" not in venue

    def test_no_venue_word_means_no_guess(self):
        html = """<html><body><div class="card">
            <a href="/e/1"></a><h3>Bir Oyun</h3>
            <span class="tarih">18 Eylül 2026</span>
            <div class="info">Harika bir gece sizi bekliyor</div>
            </div></body></html>"""
        assert self._extract(html)[0].venue is None

    def test_date_chunk_not_taken_as_venue(self):
        html = """<html><body><div class="card">
            <a href="/e/1"></a><h3>Bir Oyun</h3>
            <span class="tarih">18 Eylül 2026 · Tiyatro Sahnesi Salı</span>
            </div></body></html>"""
        venue = self._extract(html)[0].venue
        assert venue is None or "2026" not in venue

    def test_explicit_selector_wins(self):
        html = """<html><body><div class="card">
            <a href="/e/1"></a><h3>Bir Oyun</h3>
            <span class="tarih">18 Eylül 2026</span>
            <span class="mekan">İzmir Sanat</span>
            <div class="info">Konak Sahnesi · 400 TL</div>
            </div></body></html>"""
        selectors = Selectors(item=".card", title="h3", url="a@href", date=".tarih", venue=".mekan")
        assert extract_selectors(html, BASE, "test", selectors)[0].venue == "İzmir Sanat"


class TestPriceFallback:
    """Sorun 4: fiyat seçicisi tutmayınca kart metnine bakılmıyordu."""

    def test_price_read_from_card_text_when_selector_misses(self):
        html = """<html><body><div class="card">
            <a href="/e/1"></a><h3>Bir Konser</h3>
            <span class="tarih">18 Eylül 2026</span>
            <div class="tutar">750 TL</div>
            </div></body></html>"""
        # price seçicisi kasten yanlış:
        selectors = Selectors(
            item=".card", title="h3", url="a@href", date=".tarih", price=".olmayan-sinif"
        )
        events = extract_selectors(html, BASE, "test", selectors)
        event = build_event(events[0], ref=REF)
        assert event.price_min == 750.0

    def test_free_read_from_card_text(self):
        html = """<html><body><div class="card">
            <a href="/e/1"></a><h3>Ücretsiz Söyleşi</h3>
            <span class="tarih">18 Eylül 2026</span>
            <div>Giriş Serbest</div>
            </div></body></html>"""
        selectors = Selectors(item=".card", title="h3", url="a@href", date=".tarih")
        events = extract_selectors(html, BASE, "test", selectors)
        assert build_event(events[0], ref=REF).is_free

    def test_card_dates_not_mistaken_for_price(self):
        html = """<html><body><div class="card">
            <a href="/e/1"></a><h3>Bir Konser</h3>
            <span class="tarih">18 Eylül 2026 saat 21:00</span>
            <div>650 TL</div>
            </div></body></html>"""
        selectors = Selectors(item=".card", title="h3", url="a@href", date=".tarih")
        events = extract_selectors(html, BASE, "test", selectors)
        event = build_event(events[0], ref=REF)
        assert event.price_min == 650.0
        assert event.price_max is None

    def test_year_before_amount_not_merged_into_price(self):
        """ "… 2026 750 TL" metni 26.750 ₺ olarak okunmamalı (boşluk binlik ayracı değil)."""
        from izmir_events.util.money import parse_price

        price = parse_price("Bir Konser 18 Eylül 2026 750 TL")
        assert price.min_amount == 750.0

    def test_no_price_signal_stays_unknown(self):
        html = """<html><body><div class="card">
            <a href="/e/1"></a><h3>Bir Konser</h3>
            <span class="tarih">18 Eylül 2026</span>
            </div></body></html>"""
        selectors = Selectors(item=".card", title="h3", url="a@href", date=".tarih")
        events = extract_selectors(html, BASE, "test", selectors)
        assert build_event(events[0], ref=REF).price.unknown


class TestScreenshotScenarioEndToEnd:
    """Ekran görüntüsündeki sayfanın taklidi: hepsi birlikte düzelmeli."""

    HTML = """<html><body>
      <div class="event-card">
        <a href="/e/1">29 Ağustos 2026</a>
        <h3>Bu Hikaye Senden Uzun Osman</h3>
        <span class="date">29 Ağustos 2026</span>
        <div class="info">Konak Sahnesi · 400 TL</div>
      </div>
      <div class="event-card">
        <a href="/e/2">Konken Partisi İzmir / Bostanlı Suat Taşer Tiyatrosu 18 Eylül 2026</a>
        <span class="date">18 Eylül 2026</span>
        <div class="info">Ücretsiz</div>
      </div>
      <div class="event-card">
        <a href="/e/3">30 Ağustos 2026</a>
        <span class="date">30 Ağustos 2026</span>
      </div>
    </body></html>"""

    @respx.mock
    async def test_pipeline_produces_clean_events(self):
        respx.get("https://x.test/liste").mock(return_value=httpx.Response(200, text=self.HTML))
        source = SourceConfig(
            key="test",
            name="Test",
            base_url="https://x.test/liste",
            listing_urls=["https://x.test/liste"],
            strategies=["selectors"],
            selectors=Selectors(item=".event-card", title="a", url="a@href", date=".date"),
        )
        async with HttpClient(delay_seconds=0.0, respect_robots=False, cache_dir=None) as client:
            result = await scrape_source(client, source, ref=REF)

        titles = [e.title for e in result.events]
        # Sadece tarihten ibaret kart elenmeli (3. kart), diğer ikisi kalmalı.
        assert "30 Ağustos 2026" not in titles
        assert "Bu Hikaye Senden Uzun Osman" in titles
        # Üçüncü kartın tüm başlık adayları tarih olduğu için hiç kayıt üretmez.
        assert result.raw_count == 2

        konken = next(e for e in result.events if "Konken" in e.title)
        assert "2026" not in konken.title
        assert konken.venue == "Bostanlı Suat Taşer Tiyatrosu"
        assert konken.is_free

        osman = next(e for e in result.events if "Osman" in e.title)
        assert osman.price_min == 400.0

    def test_heuristic_strategy_handles_same_page(self):
        events = extract_heuristic(self.HTML, BASE, "test")
        built = [build_event(r, ref=REF) for r in events]
        titles = [e.title for e in built if e]
        assert "Bu Hikaye Senden Uzun Osman" in titles
