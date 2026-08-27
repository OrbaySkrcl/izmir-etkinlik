"""Yapılandırma yükleme testleri."""

from pathlib import Path

import pytest

from izmir_events.config import (
    Settings,
    SourceConfig,
    _find_sources_file,
    enabled_sources,
    load_sources,
)

CONFIG = Path(__file__).parents[1] / "config" / "sources.yaml"


class TestSourcesFile:
    def test_repo_config_is_valid(self):
        sources = load_sources(CONFIG)
        assert len(sources) >= 8
        assert all(isinstance(s, SourceConfig) for s in sources)

    def test_keys_are_unique(self):
        keys = [s.key for s in load_sources(CONFIG)]
        assert len(keys) == len(set(keys))

    def test_user_provided_sources_present(self):
        """Kullanıcının verdiği altı kaynak tanımlı ve açık olmalı."""
        expected = {"kultursanat", "bubilet", "biletinial", "oggusto", "biletimgo", "izmirmag"}
        enabled = {s.key for s in load_sources(CONFIG) if s.enabled}
        assert expected <= enabled

    def test_every_source_has_listing_url(self):
        for source in load_sources(CONFIG):
            assert source.urls, source.key
            assert all(u.startswith("http") for u in source.urls), source.key

    def test_every_source_has_strategies(self):
        for source in load_sources(CONFIG):
            assert source.strategies, source.key

    def test_other_city_exclusion_on_by_default(self):
        """Bu bir İzmir botu: başka şehir kayıtları varsayılan olarak elenmeli."""
        for source in load_sources(CONFIG):
            assert source.exclude_other_cities, source.key

    def test_disabled_sources_document_why(self):
        """Kapalı kaynakların notunda gerekçe olmalı."""
        for source in load_sources(CONFIG):
            if not source.enabled:
                assert source.notes.strip(), source.key

    def test_selector_based_sources_define_item(self):
        """'selectors' stratejisi açık olan kaynakta item seçicisi olmalı."""
        for source in load_sources(CONFIG):
            if "selectors" in source.strategies and source.enabled:
                assert source.selectors.item, source.key

    def test_missing_file_gives_helpful_error(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="SOURCES_FILE"):
            load_sources(tmp_path / "yok.yaml")

    def test_find_sources_file_locates_repo_config(self):
        assert _find_sources_file().is_file()

    def test_only_sources_env_filters(self, monkeypatch):
        monkeypatch.setenv("ONLY_SOURCES", "bubilet,izmirmag")
        monkeypatch.setenv("SOURCES_FILE", str(CONFIG))
        from izmir_events import config as config_module

        config_module.get_settings.cache_clear()
        try:
            assert {s.key for s in enabled_sources(CONFIG)} == {"bubilet", "izmirmag"}
        finally:
            config_module.get_settings.cache_clear()


class TestSettings:
    def test_railway_postgres_url_converted_to_async(self):
        settings = Settings(database_url="postgres://user:pw@host:5432/db")
        assert settings.database_url.startswith("postgresql+asyncpg://")

    def test_postgresql_url_converted(self):
        settings = Settings(database_url="postgresql://user:pw@host/db")
        assert settings.database_url.startswith("postgresql+asyncpg://")

    def test_sqlite_url_converted(self):
        settings = Settings(database_url="sqlite:///data/x.db")
        assert settings.database_url == "sqlite+aiosqlite:///data/x.db"

    def test_async_url_left_alone(self):
        url = "postgresql+asyncpg://user:pw@host/db"
        assert Settings(database_url=url).database_url == url

    def test_admin_ids_parsed(self):
        assert Settings(telegram_admin_ids="1, 2 ,3").admin_ids == {1, 2, 3}

    def test_admin_ids_ignores_junk(self):
        assert Settings(telegram_admin_ids="abc,,7").admin_ids == {7}

    def test_empty_admin_ids(self):
        assert Settings(telegram_admin_ids="").admin_ids == set()

    def test_webhook_mode_detection(self):
        assert not Settings().use_webhook
        assert Settings(webhook_url="https://x.test").use_webhook

    def test_database_label(self):
        assert Settings(database_url="postgres://u:p@h/d").database_label == "PostgreSQL"
        assert Settings(database_url="sqlite:///x.db").database_label == "SQLite"

    def test_sqlite_marked_as_not_persistent(self):
        # Railway'de SQLite kapsayıcıyla birlikte silinir; /durum bunu göstermeli.
        assert not Settings(database_url="sqlite:///x.db").database_is_persistent
        assert Settings(database_url="postgres://u:p@h/d").database_is_persistent

    def test_is_sqlite(self):
        assert Settings(database_url="sqlite:///x.db").is_sqlite
        assert not Settings(database_url="postgres://u@h/d").is_sqlite
