"""CLI testleri (typer CliRunner ile, ağa çıkmaz)."""

from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from izmir_events.cli import app

runner = CliRunner()
CONFIG = Path(__file__).parents[1] / "config" / "sources.yaml"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Her test kendi veritabanı ve ayarlarıyla çalışsın."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("SOURCES_FILE", str(CONFIG))
    monkeypatch.setenv("RESPECT_ROBOTS", "false")
    monkeypatch.setenv("HTTP_DELAY_SECONDS", "0")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    # rich tabloları dar terminalde sarar; test çıktısı okunabilir kalsın.
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("ONLY_SOURCES", raising=False)

    from izmir_events import config as config_module
    from izmir_events.store import db as db_module

    config_module.get_settings.cache_clear()
    db_module._engine = None
    db_module._session_factory = None
    yield
    config_module.get_settings.cache_clear()


class TestSimpleCommands:
    def test_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "doctor" in result.stdout

    def test_sources_lists_configured_sources(self):
        result = runner.invoke(app, ["sources"])
        assert result.exit_code == 0
        assert "kultursanat" in result.stdout
        assert "bubilet" in result.stdout

    def test_initdb(self):
        result = runner.invoke(app, ["initdb"])
        assert result.exit_code == 0
        assert "hazır" in result.stdout

    def test_stats_on_empty_db(self):
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 0
        assert "Gelecek etkinlik" in result.stdout

    def test_list_on_empty_db(self):
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "Kayıt bulunamadı" in result.stdout

    def test_list_rejects_bad_bucket(self):
        result = runner.invoke(app, ["list", "--bucket", "olmayan_kova"])
        assert result.exit_code == 1
        assert "Geçersiz kova" in result.stdout


class TestScrapeCommand:
    @respx.mock
    def test_scrape_stores_and_prints_table(self):
        html = """<html><body><div class="etkinlik-item"><a href="/1"></a>
            <h3 class="etkinlik-adi">CLI Test Konseri</h3>
            <span class="etkinlik-tarih">31 Aralık 2030</span>
            <span class="etkinlik-yer">İzmir Sanat</span></div></body></html>"""
        respx.route(host__in=["kultursanat.izmir.bel.tr"]).mock(
            return_value=httpx.Response(200, text=html)
        )
        result = runner.invoke(app, ["scrape", "--source", "kultursanat", "--no-cache"])
        assert result.exit_code == 0
        assert "CLI Test Konseri" in result.stdout
        assert "benzersiz etkinlik" in result.stdout

        listed = runner.invoke(app, ["list", "--bucket", "ileride"])
        assert "CLI Test Konseri" in listed.stdout

    def test_scrape_rejects_unknown_source(self):
        result = runner.invoke(app, ["scrape", "--source", "boyle-bir-kaynak-yok"])
        assert result.exit_code == 1
        assert "Eşleşen kaynak yok" in result.stdout


class TestDoctorCommand:
    def test_unknown_source_lists_valid_ones(self):
        result = runner.invoke(app, ["doctor", "--source", "yok"])
        assert result.exit_code == 1
        assert "Bilinmeyen kaynak" in result.stdout
        assert "bubilet" in result.stdout

    @respx.mock
    def test_reports_working_strategy(self, fixtures):
        html = (fixtures / "jsonld_listing.html").read_text(encoding="utf-8")
        respx.get("https://x.test/liste").mock(return_value=httpx.Response(200, text=html))
        result = runner.invoke(
            app, ["doctor", "--source", "bubilet", "--url", "https://x.test/liste"]
        )
        assert result.exit_code == 0
        assert "jsonld" in result.stdout
        assert "En iyi strateji" in result.stdout

    @respx.mock
    def test_fetch_failure_exits_nonzero(self):
        respx.get("https://x.test/bos").mock(return_value=httpx.Response(500))
        result = runner.invoke(
            app, ["doctor", "--source", "bubilet", "--url", "https://x.test/bos"]
        )
        assert result.exit_code == 1
        assert "İndirilemedi" in result.stdout

    @respx.mock
    def test_no_events_gives_actionable_advice(self):
        respx.get("https://x.test/bos").mock(
            return_value=httpx.Response(200, text="<html><body>boş sayfa</body></html>")
        )
        result = runner.invoke(
            app, ["doctor", "--source", "bubilet", "--url", "https://x.test/bos"]
        )
        assert result.exit_code == 2
        assert "sources.yaml" in result.stdout

    @respx.mock
    def test_save_html_writes_file(self, fixtures, tmp_path, monkeypatch):
        html = (fixtures / "jsonld_listing.html").read_text(encoding="utf-8")
        respx.get("https://x.test/liste").mock(return_value=httpx.Response(200, text=html))
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            ["doctor", "--source", "bubilet", "--url", "https://x.test/liste", "--save-html"],
        )
        assert result.exit_code == 0
        assert (tmp_path / "bubilet.html").exists()
