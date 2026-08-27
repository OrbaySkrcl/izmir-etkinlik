"""Uçtan uca toplama hattı testleri (sahte HTTP ile)."""

from datetime import date

import httpx
import pytest
import respx

from izmir_events.config import Pagination, Selectors, SourceConfig
from izmir_events.pipeline import collect, collect_and_store
from izmir_events.scrape.http import HttpClient
from izmir_events.scrape.runner import paged_urls, run_extractors, scrape_source
from izmir_events.store import repo
from izmir_events.store.db import session_scope
from izmir_events.util.dates import Bucket

REF = date(2026, 8, 27)


def _source(key: str, url: str, **kwargs) -> SourceConfig:
    defaults: dict = {
        "key": key,
        "name": key.title(),
        "base_url": url,
        "listing_urls": [url],
        "strategies": ["jsonld", "nextdata", "selectors", "heuristic"],
        "selectors": Selectors(
            item=".etkinlik-item, .card, [class*='ListCard']",
            title="h3, h4, .etkinlik-adi",
            url="a@href",
            date=".etkinlik-tarih, span",
            venue=".etkinlik-yer",
            price=".fiyat",
        ),
    }
    defaults.update(kwargs)
    return SourceConfig(**defaults)


@pytest.fixture
def client_kwargs() -> dict:
    return {
        "delay_seconds": 0.0,
        "max_retries": 1,
        "respect_robots": False,
        "cache_dir": None,
    }


class TestHttpClient:
    @respx.mock
    async def test_fetch_ok(self, client_kwargs):
        respx.get("https://x.test/a").mock(return_value=httpx.Response(200, text="<html>ok</html>"))
        async with HttpClient(**client_kwargs) as client:
            result = await client.fetch("https://x.test/a", use_cache=False)
        assert result.ok
        assert "ok" in result.text

    @respx.mock
    async def test_404_not_retried(self, client_kwargs):
        route = respx.get("https://x.test/yok").mock(return_value=httpx.Response(404))
        async with HttpClient(**client_kwargs) as client:
            result = await client.fetch("https://x.test/yok", use_cache=False)
        assert not result.ok
        assert route.call_count == 1

    @respx.mock
    async def test_server_error_retried(self, client_kwargs):
        route = respx.get("https://x.test/500").mock(return_value=httpx.Response(503))
        async with HttpClient(**{**client_kwargs, "max_retries": 2}) as client:
            result = await client.fetch("https://x.test/500", use_cache=False)
        assert not result.ok
        assert route.call_count == 2

    @respx.mock
    async def test_network_error_returns_result_not_raise(self, client_kwargs):
        respx.get("https://x.test/kopuk").mock(side_effect=httpx.ConnectError("bağlantı yok"))
        async with HttpClient(**client_kwargs) as client:
            result = await client.fetch("https://x.test/kopuk", use_cache=False)
        assert not result.ok
        assert result.error is not None

    @respx.mock
    async def test_cache_prevents_second_request(self, tmp_path, client_kwargs):
        route = respx.get("https://x.test/c").mock(
            return_value=httpx.Response(200, text="<html>önbellek</html>")
        )
        kwargs = {**client_kwargs, "cache_dir": tmp_path, "cache_ttl_seconds": 600}
        async with HttpClient(**kwargs) as client:
            first = await client.fetch("https://x.test/c")
            second = await client.fetch("https://x.test/c")
        assert route.call_count == 1
        assert second.from_cache
        assert first.text == second.text

    @respx.mock
    async def test_robots_disallow_blocks_fetch(self):
        respx.get("https://x.test/robots.txt").mock(
            return_value=httpx.Response(200, text="User-agent: *\nDisallow: /gizli")
        )
        page = respx.get("https://x.test/gizli/sayfa").mock(
            return_value=httpx.Response(200, text="<html>gizli</html>")
        )
        async with HttpClient(delay_seconds=0.0, respect_robots=True, cache_dir=None) as client:
            result = await client.fetch("https://x.test/gizli/sayfa", use_cache=False)
        assert not result.ok
        assert page.call_count == 0


class TestHeaderSafety:
    @respx.mock
    async def test_non_ascii_user_agent_does_not_break_requests(self, client_kwargs):
        """HTTP başlıkları ASCII olmak zorunda; Türkçe karakter sızarsa istek patlar."""
        respx.get("https://x.test/a").mock(return_value=httpx.Response(200, text="<html>ok</html>"))
        async with HttpClient(user_agent="İzmirBot/1.0 (kişisel)", **client_kwargs) as client:
            result = await client.fetch("https://x.test/a", use_cache=False)
        assert result.ok

    def test_default_user_agent_is_ascii(self):
        from izmir_events.config import Settings

        Settings().user_agent.encode("ascii")  # istisna atmamalı


class TestPagination:
    def test_query_param_pagination(self):
        source = _source(
            "s", "https://x.test/liste", pagination=Pagination(param="page", start=1, max_pages=3)
        )
        assert paged_urls(source) == [
            "https://x.test/liste",
            "https://x.test/liste?page=2",
            "https://x.test/liste?page=3",
        ]

    def test_template_pagination(self):
        source = _source(
            "s",
            "https://x.test/liste",
            pagination=Pagination(
                template="https://x.test/liste/sayfa/{page}", start=1, max_pages=2
            ),
        )
        assert paged_urls(source)[1] == "https://x.test/liste/sayfa/2"

    def test_no_pagination(self):
        assert paged_urls(_source("s", "https://x.test/liste")) == ["https://x.test/liste"]

    def test_existing_query_preserved(self):
        source = _source(
            "s",
            "https://x.test/liste?sehir=izmir",
            pagination=Pagination(param="page", start=1, max_pages=2),
        )
        assert "sehir=izmir&page=2" in paged_urls(source)[1]


class TestStrategySelection:
    def test_richest_strategy_wins(self, fixtures):
        """JSON-LD tek etkinlik, HTML listesi üç etkinlik verirse HTML kazanmalı."""
        html = (fixtures / "selectors_listing.html").read_text(encoding="utf-8")
        html = html.replace(
            "</head>",
            '<script type="application/ld+json">'
            '{"@type":"Event","name":"Öne Çıkan Tek Etkinlik","startDate":"2026-09-12"}'
            "</script></head>",
        )
        source = _source("s", "https://x.test/")
        raws, strategy = run_extractors(html, "https://x.test/", source)
        assert strategy == "selectors"
        assert len(raws) == 3

    def test_returns_none_when_nothing_found(self):
        source = _source("s", "https://x.test/")
        raws, strategy = run_extractors("<html><body>boş</body></html>", "https://x.test/", source)
        assert raws == []
        assert strategy is None

    def test_disabled_strategy_not_used(self, fixtures):
        html = (fixtures / "jsonld_listing.html").read_text(encoding="utf-8")
        source = _source("s", "https://x.test/", strategies=["selectors"])
        _, strategy = run_extractors(html, "https://x.test/", source)
        assert strategy != "jsonld"


class TestScrapeSource:
    @respx.mock
    async def test_scrapes_and_filters(self, fixtures, client_kwargs):
        html = (fixtures / "selectors_listing.html").read_text(encoding="utf-8")
        respx.get("https://x.test/liste").mock(return_value=httpx.Response(200, text=html))
        source = _source("s", "https://x.test/liste")
        async with HttpClient(**client_kwargs) as client:
            result = await scrape_source(client, source, ref=REF)
        assert result.ok
        assert len(result.events) == 3
        assert result.strategy == "selectors"
        assert result.pages_fetched == 1

    @respx.mock
    async def test_past_events_dropped(self, client_kwargs):
        html = """<html><body><div class="etkinlik-item">
            <a href="/1"></a><h3 class="etkinlik-adi">Geçmiş Konser</h3>
            <span class="etkinlik-tarih">1 Ocak 2026</span></div></body></html>"""
        respx.get("https://x.test/liste").mock(return_value=httpx.Response(200, text=html))
        source = _source("s", "https://x.test/liste")
        async with HttpClient(**client_kwargs) as client:
            result = await scrape_source(client, source, ref=REF)
        assert result.events == []
        assert result.dropped_filtered == 1

    @respx.mock
    async def test_navigation_junk_dropped(self, client_kwargs):
        html = """<html><body>
            <div class="etkinlik-item"><a href="/1"></a><h3 class="etkinlik-adi">Anasayfa</h3>
              <span class="etkinlik-tarih">12 Eylül 2026</span></div>
            <div class="etkinlik-item"><a href="/2"></a><h3 class="etkinlik-adi">Gerçek Konser</h3>
              <span class="etkinlik-tarih">12 Eylül 2026</span></div>
            </body></html>"""
        respx.get("https://x.test/liste").mock(return_value=httpx.Response(200, text=html))
        source = _source("s", "https://x.test/liste")
        async with HttpClient(**client_kwargs) as client:
            result = await scrape_source(client, source, ref=REF)
        assert [e.title for e in result.events] == ["Gerçek Konser"]

    @respx.mock
    async def test_city_filter_drops_other_cities(self, client_kwargs):
        html = """<html><body>
            <div class="etkinlik-item"><a href="/1"></a><h3 class="etkinlik-adi">Ankara Konseri</h3>
              <span class="etkinlik-tarih">12 Eylül 2026</span>
              <span class="etkinlik-yer">Ankara Arena</span></div>
            <div class="etkinlik-item"><a href="/2"></a>
              <h3 class="etkinlik-adi">Bornova Konseri</h3>
              <span class="etkinlik-tarih">12 Eylül 2026</span>
              <span class="etkinlik-yer">Bornova Sahne</span></div>
            </body></html>"""
        respx.get("https://x.test/liste").mock(return_value=httpx.Response(200, text=html))
        source = _source("s", "https://x.test/liste", city_filter=True)
        async with HttpClient(**client_kwargs) as client:
            result = await scrape_source(client, source, ref=REF)
        assert [e.title for e in result.events] == ["Bornova Konseri"]

    @respx.mock
    async def test_http_failure_recorded_not_raised(self, client_kwargs):
        respx.get("https://x.test/liste").mock(return_value=httpx.Response(500))
        source = _source("s", "https://x.test/liste")
        async with HttpClient(**client_kwargs) as client:
            result = await scrape_source(client, source, ref=REF)
        assert not result.ok
        assert result.errors

    @respx.mock
    async def test_free_by_default_applied(self, client_kwargs):
        html = """<html><body><div class="etkinlik-item">
            <a href="/1"></a><h3 class="etkinlik-adi">Belediye Konseri</h3>
            <span class="etkinlik-tarih">12 Eylül 2026</span></div></body></html>"""
        respx.get("https://x.test/liste").mock(return_value=httpx.Response(200, text=html))
        source = _source("s", "https://x.test/liste", free_by_default=True)
        async with HttpClient(**client_kwargs) as client:
            result = await scrape_source(client, source, ref=REF)
        assert result.events[0].is_free


class TestCollect:
    @respx.mock
    async def test_two_sources_deduplicated(self, settings, fixtures):
        """İki farklı kaynak aynı konseri farklı yazımla verirse tek kayıt çıkmalı."""
        html_a = """<html><body><div class="etkinlik-item">
            <a href="/a/1"></a><h3 class="etkinlik-adi">Sezen Aksu</h3>
            <span class="etkinlik-tarih">12 Eylül 2026</span>
            <span class="etkinlik-yer">Kültürpark Açıkhava Tiyatrosu</span>
            <span class="fiyat">450 TL</span></div></body></html>"""
        html_b = """<html><body><div class="card">
            <a href="/b/9"></a><h3 class="etkinlik-adi">Sezen Aksu Konseri</h3>
            <span class="etkinlik-tarih">12.09.2026</span>
            <span class="etkinlik-yer">Kültürpark</span>
            <span class="fiyat">1200 TL</span></div></body></html>"""
        respx.get("https://a.test/liste").mock(return_value=httpx.Response(200, text=html_a))
        respx.get("https://b.test/liste").mock(return_value=httpx.Response(200, text=html_b))

        sources = [
            _source("a", "https://a.test/liste", priority=80),
            _source("b", "https://b.test/liste", priority=40),
        ]
        result = await collect(settings, sources=sources, ref=REF, use_cache=False)

        assert result.raw_count == 2
        assert len(result.events) == 1
        merged = result.events[0]
        assert merged.source_count == 2
        assert merged.price_min == 450.0
        assert merged.price_max == 1200.0

    @respx.mock
    async def test_one_source_down_does_not_break_run(self, settings):
        html = """<html><body><div class="etkinlik-item">
            <a href="/1"></a><h3 class="etkinlik-adi">Çalışan Kaynak Konseri</h3>
            <span class="etkinlik-tarih">12 Eylül 2026</span></div></body></html>"""
        respx.get("https://ok.test/liste").mock(return_value=httpx.Response(200, text=html))
        respx.get("https://down.test/liste").mock(side_effect=httpx.ConnectError("yok"))

        sources = [
            _source("ok", "https://ok.test/liste"),
            _source("down", "https://down.test/liste"),
        ]
        result = await collect(settings, sources=sources, ref=REF, use_cache=False)

        assert len(result.events) == 1
        assert len(result.failed_sources) == 1
        assert result.failed_sources[0].key == "down"
        assert "down" in result.report()

    @respx.mock
    async def test_report_is_human_readable(self, settings):
        respx.get("https://a.test/liste").mock(
            return_value=httpx.Response(200, text="<html></html>")
        )
        result = await collect(
            settings, sources=[_source("a", "https://a.test/liste")], ref=REF, use_cache=False
        )
        report = result.report()
        assert "benzersiz etkinlik" in report
        assert "Kaynaklar:" in report


class TestCollectAndStore:
    @respx.mock
    async def test_end_to_end(self, db, settings, monkeypatch):
        """Tara -> tekilleştir -> kaydet -> sorgula zincirinin tamamı."""
        html_a = """<html><body>
            <div class="etkinlik-item"><a href="/a/1"></a>
              <h3 class="etkinlik-adi">Bugünkü Ücretsiz Konser</h3>
              <span class="etkinlik-tarih">27 Ağustos 2026</span>
              <span class="etkinlik-yer">İzmir Sanat</span>
              <span class="fiyat">Ücretsiz</span></div>
            <div class="etkinlik-item"><a href="/a/2"></a>
              <h3 class="etkinlik-adi">Gelecek Hafta Tiyatro Oyunu</h3>
              <span class="etkinlik-tarih">3 Eylül 2026</span>
              <span class="etkinlik-yer">Konak Sahnesi</span>
              <span class="fiyat">200 TL</span></div>
            </body></html>"""
        html_b = """<html><body><div class="card"><a href="/b/1"></a>
            <h3 class="etkinlik-adi">Bugünkü Ücretsiz Konser Etkinliği</h3>
            <span class="etkinlik-tarih">27.08.2026</span>
            <span class="etkinlik-yer">İzmir Sanat</span></div></body></html>"""
        respx.get("https://a.test/liste").mock(return_value=httpx.Response(200, text=html_a))
        respx.get("https://b.test/liste").mock(return_value=httpx.Response(200, text=html_b))

        # Sabit "bugün" kullan ki test tarihe bağlı kalmasın.
        import izmir_events.pipeline as pipeline_module

        monkeypatch.setattr(pipeline_module, "today", lambda: REF)

        sources = [
            _source("a", "https://a.test/liste", priority=80),
            _source("b", "https://b.test/liste", priority=40),
        ]
        result = await collect_and_store(settings, sources=sources, ref=REF, use_cache=False)

        assert result.raw_count == 3
        assert len(result.events) == 2
        assert result.inserted == 2
        assert len(result.new_uids) == 2

        async with session_scope() as session:
            today_events = await repo.get_events_for_bucket(session, Bucket.TODAY, ref=REF)
            counts = await repo.count_events(session, ref=REF)
            health = await repo.source_health(session)
            run = await repo.last_run(session)

        assert len(today_events) == 1
        assert today_events[0].source_count == 2  # iki kaynakta doğrulandı
        assert today_events[0].is_free
        assert counts["gelecek"] == 2
        assert {h.source_key for h in health} == {"a", "b"}
        assert run.unique_count == 2

    @respx.mock
    async def test_second_run_reports_no_new_events(self, db, settings, monkeypatch):
        html = """<html><body><div class="etkinlik-item"><a href="/1"></a>
            <h3 class="etkinlik-adi">Tekrarlayan Konser</h3>
            <span class="etkinlik-tarih">12 Eylül 2026</span></div></body></html>"""
        respx.get("https://a.test/liste").mock(return_value=httpx.Response(200, text=html))
        import izmir_events.pipeline as pipeline_module

        monkeypatch.setattr(pipeline_module, "today", lambda: REF)
        sources = [_source("a", "https://a.test/liste")]

        first = await collect_and_store(settings, sources=sources, ref=REF, use_cache=False)
        second = await collect_and_store(settings, sources=sources, ref=REF, use_cache=False)

        assert first.inserted == 1
        assert second.inserted == 0
        assert second.updated == 1
