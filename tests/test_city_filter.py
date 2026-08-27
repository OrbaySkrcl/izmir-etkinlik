"""Şehir filtresi testleri.

Canlı botta İstanbul etkinlikleri İzmir listesine sızıyordu:
"Adamlar İstanbul Avrupa / Harbiye Cemil Topuzlu Açıkhava Sahnesi",
"Bu Hikaye Senden Uzun Osman" (Moda Sahnesi), "Zengin Mutfağı / Dasdas Sahne".
"""

from datetime import date

import httpx
import pytest
import respx

from izmir_events.config import Selectors, SourceConfig
from izmir_events.models import RawEvent, build_event
from izmir_events.scrape.http import HttpClient
from izmir_events.scrape.runner import _is_other_city, _mentions_izmir, scrape_source
from izmir_events.util.text import strip_trailing_tags, strip_trailing_venue

REF = date(2026, 8, 27)


def _raw(title: str, venue: str | None = None, url: str | None = None) -> RawEvent:
    return RawEvent(source="t", title=title, venue=venue, url=url)


class TestOtherCityDetection:
    @pytest.mark.parametrize(
        ("title", "venue"),
        [
            ("Adamlar İstanbul Avrupa / Harbiye Cemil Topuzlu Açıkhava Sahnesi GÜNCEL", None),
            ("Aşk Listesi İstanbul Anadolu / Kadıköy Selamiçeşme Özgürlük Parkı", None),
            ("Zengin Mutfağı İstanbul Anadolu / Dasdas Sahne", None),
            ("Bu Hikaye Senden Uzun Osman", "Moda Sahnesi Büyük Salon"),
            ("52 Hertz", "Moda Sahnesi Büyük Salon"),
            ("Bir Konser", "Ankara Congresium"),
            ("Bir Oyun", "Bursa Devlet Tiyatrosu"),
        ],
    )
    def test_other_city_events_excluded(self, title, venue):
        assert _is_other_city(_raw(title, venue))

    @pytest.mark.parametrize(
        ("title", "venue"),
        [
            # Şehri belirsiz: korunmalı, çünkü İzmir etkinliklerinin çoğu
            # adında "İzmir" geçirmiyor.
            ("Azat Bozkurt – Tek Kişilik Stand Up", None),
            ("Chopstick Night", None),
            ("İsmail Türküsev Stand-up", None),
            # Açıkça İzmir
            ("Bornova 4'lü Stand Up Gecesi", None),
            ("Teyfik Rodos İle Tanju Okan Şarkıları", "Urladam Ağaçlı Sahne"),
            ("Sezen Aksu", "Kültürpark Açıkhava Tiyatrosu"),
            ("BRIGHT TALK İZMİR - English Speaking Club - BOSTANLI", None),
            # İki şehir birden geçiyorsa İzmir kazanır
            ("İstanbul Devlet Tiyatrosu İzmir Turnesi", None),
        ],
    )
    def test_izmir_and_unknown_events_kept(self, title, venue):
        assert not _is_other_city(_raw(title, venue))

    def test_selamicesme_does_not_match_cesme(self):
        """İstanbul'un Selamiçeşme semti, İzmir'in Çeşme'sine eşleşmemeli."""
        event = _raw("Aşk Listesi İstanbul / Kadıköy Selamiçeşme Özgürlük Parkı")
        assert not _mentions_izmir(event)
        assert _is_other_city(event)

    def test_bostanli_does_not_match_bostanci(self):
        """İzmir'in Bostanlı'sı, İstanbul'un Bostancı'sına eşleşmemeli."""
        assert not _is_other_city(_raw("Bir Etkinlik", "Bostanlı Suat Taşer Tiyatrosu"))

    @pytest.mark.parametrize(
        "title",
        ["Aydınlık Bir Gece", "Ordumuz Şenliği", "Konaklama Dahil Festival", "Vanilya Partisi"],
    )
    def test_common_words_not_mistaken_for_cities(self, title):
        assert not _is_other_city(_raw(title))

    def test_izmir_suffix_forms_recognised(self):
        for title in ["İzmir'de Yaz", "İzmirde Bir Gece", "İzmir Marşı"]:
            assert _mentions_izmir(_raw(title)), title


class TestTitleTailCleanup:
    def test_venue_duplicate_stripped(self):
        assert (
            strip_trailing_venue("Adamlar / Harbiye Açıkhava Sahnesi", "Harbiye Açıkhava Sahnesi")
            == "Adamlar"
        )

    def test_venue_not_stripped_when_not_a_suffix(self):
        title = "Hamlet"
        assert strip_trailing_venue(title, "İzmir Sanat") == title

    def test_venue_not_stripped_when_nothing_would_remain(self):
        title = "Konak Sahnesi"
        assert strip_trailing_venue(title, "Konak Sahnesi") == title

    def test_listing_tags_stripped(self):
        assert strip_trailing_tags("Sezen Aksu İzmir Avrupa GÜNCEL") == "Sezen Aksu"

    def test_lowercase_tag_stripped(self):
        assert strip_trailing_tags("Aşk Listesi güncel") == "Aşk Listesi"

    def test_leading_guncel_preserved(self):
        assert strip_trailing_tags("Güncel Sanat Sergisi") == "Güncel Sanat Sergisi"

    def test_city_kept_without_flag(self):
        # "Elveda İstanbul" gerçek bir oyun adı olabilir.
        assert strip_trailing_tags("Elveda İstanbul") == "Elveda İstanbul"

    def test_city_dropped_with_flag(self):
        assert strip_trailing_tags("Adamlar İzmir", drop_city=True) == "Adamlar"

    def test_build_event_cleans_full_pattern(self):
        event = build_event(
            _raw(
                "Adamlar İzmir / Kültürpark Açıkhava Sahnesi GÜNCEL",
                "Kültürpark Açıkhava Sahnesi GÜNCEL",
            ).model_copy(update={"date_text": "12 Eylül 2026"}),
            ref=REF,
        )
        assert event.title == "Adamlar"
        assert event.venue == "Kültürpark Açıkhava Sahnesi"

    def test_build_event_keeps_real_city_title(self):
        event = build_event(
            _raw("Merhaba İzmir").model_copy(update={"date_text": "12 Eylül 2026"}), ref=REF
        )
        assert event.title == "Merhaba İzmir"


class TestScrapeSourceFiltering:
    HTML = """<html><body>
      <div class="card">
        <a href="/e/1"></a><h3>Adamlar İstanbul Avrupa / Harbiye Cemil Topuzlu Sahnesi</h3>
        <span class="tarih">12 Eylül 2026</span>
      </div>
      <div class="card">
        <a href="/e/2"></a><h3>Bu Hikaye Senden Uzun Osman</h3>
        <span class="tarih">12 Eylül 2026</span>
        <span class="mekan">Moda Sahnesi Büyük Salon</span>
      </div>
      <div class="card">
        <a href="/e/3"></a><h3>Bornova 4'lü Stand Up Gecesi</h3>
        <span class="tarih">12 Eylül 2026</span>
      </div>
      <div class="card">
        <a href="/e/4"></a><h3>Azat Bozkurt – Tek Kişilik Stand Up</h3>
        <span class="tarih">12 Eylül 2026</span>
      </div>
    </body></html>"""

    def _source(self, **kwargs) -> SourceConfig:
        defaults: dict = {
            "key": "test",
            "name": "Test",
            "base_url": "https://x.test/liste",
            "listing_urls": ["https://x.test/liste"],
            "strategies": ["selectors"],
            "selectors": Selectors(
                item=".card", title="h3", url="a@href", date=".tarih", venue=".mekan"
            ),
        }
        defaults.update(kwargs)
        return SourceConfig(**defaults)

    @respx.mock
    async def test_other_city_events_dropped(self):
        respx.get("https://x.test/liste").mock(return_value=httpx.Response(200, text=self.HTML))
        async with HttpClient(delay_seconds=0.0, respect_robots=False, cache_dir=None) as client:
            result = await scrape_source(client, self._source(), ref=REF)

        titles = [e.title for e in result.events]
        assert "Bornova 4'lü Stand Up Gecesi" in titles
        assert "Azat Bozkurt – Tek Kişilik Stand Up" in titles
        assert not any("Adamlar" in t for t in titles)
        assert not any("Uzun Osman" in t for t in titles)
        assert result.dropped_other_city == 2

    @respx.mock
    async def test_filter_can_be_disabled_per_source(self):
        respx.get("https://x.test/liste").mock(return_value=httpx.Response(200, text=self.HTML))
        source = self._source(exclude_other_cities=False)
        async with HttpClient(delay_seconds=0.0, respect_robots=False, cache_dir=None) as client:
            result = await scrape_source(client, source, ref=REF)
        assert len(result.events) == 4
        assert result.dropped_other_city == 0

    @respx.mock
    async def test_summary_reports_dropped_count(self):
        respx.get("https://x.test/liste").mock(return_value=httpx.Response(200, text=self.HTML))
        async with HttpClient(delay_seconds=0.0, respect_robots=False, cache_dir=None) as client:
            result = await scrape_source(client, self._source(), ref=REF)
        assert "2 başka şehir" in result.summary()
