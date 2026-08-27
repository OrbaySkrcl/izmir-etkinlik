"""Ortam değişkenleri ve kaynak yapılandırması."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _find_sources_file() -> Path:
    """``sources.yaml``'ı olası konumlarda arar.

    Depo içinden (editable kurulum) çalışırken dosya ``<repo>/config/`` altında;
    Docker imajında paket site-packages'a kurulduğu için ``parents[2]`` depo
    kökünü göstermez. Bu yüzden çalışma dizini ve yaygın dağıtım yolları da
    denenir. Hiçbiri yoksa depo-göreli yol döndürülür ve hata mesajı
    ``load_sources`` tarafından üretilir.
    """
    candidates = [
        Path.cwd() / "config" / "sources.yaml",
        PROJECT_ROOT / "config" / "sources.yaml",
        Path("/app/config/sources.yaml"),
        Path(__file__).resolve().parent / "config" / "sources.yaml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return PROJECT_ROOT / "config" / "sources.yaml"


DEFAULT_SOURCES_FILE = _find_sources_file()


class Settings(BaseSettings):
    """Ortam değişkenlerinden okunan uygulama ayarları."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_admin_ids: str = ""  # virgülle ayrılmış kullanıcı id'leri

    # --- Veritabanı ---
    # Railway Postgres eklendiğinde DATABASE_URL otomatik gelir.
    database_url: str = "sqlite+aiosqlite:///data/izmir_events.db"

    # --- Toplama ---
    scrape_interval_minutes: int = 180
    scrape_on_startup: bool = True
    http_timeout: float = 25.0
    http_max_retries: int = 3
    http_concurrency: int = 4
    http_delay_seconds: float = 1.0  # aynı host'a istekler arası bekleme
    http_cache_ttl_minutes: int = 30
    respect_robots: bool = True
    # HTTP başlıkları ASCII olmak zorunda; Türkçe karakter kullanmayın.
    user_agent: str = (
        "IzmirEtkinlikBot/1.0 "
        "(+https://github.com/OrbaySkrcl/izmir-etkinlik; personal event tracker)"
    )

    # --- Tekilleştirme ---
    dedup_threshold: float = 0.82
    dedup_date_tolerance_days: int = 1

    # Son bu kadar gündür hiçbir taramada görülmeyen kayıtlar silinir.
    # 0 = kapalı. Kaynak uzun süre erişilemezse kayıtları da düşeceğinden
    # tarama aralığına göre rahat bir pay bırakın.
    prune_stale_days: int = 14

    # --- Bülten ---
    digest_hour: int = 9  # Europe/Istanbul
    digest_minute: int = 0
    max_events_per_message: int = 25

    # --- Sunum biçimi ---
    # Webhook URL verilirse bot webhook modunda çalışır (Railway'de önerilir),
    # aksi halde long polling kullanılır.
    webhook_url: str = ""
    webhook_secret: str = ""
    port: int = 8080

    # --- Diğer ---
    sources_file: str = Field(default_factory=lambda: str(_find_sources_file()))
    log_level: str = "INFO"
    timezone: str = "Europe/Istanbul"

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        """Railway'in verdiği senkron URL'i async sürücüye çevirir."""
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql://", 1)
        if v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("sqlite:///"):
            v = v.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        return v

    @property
    def admin_ids(self) -> set[int]:
        ids: set[int] = set()
        for part in self.telegram_admin_ids.replace(" ", "").split(","):
            if part.isdigit():
                ids.add(int(part))
        return ids

    @property
    def use_webhook(self) -> bool:
        return bool(self.webhook_url.strip())

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def database_label(self) -> str:
        """Hangi veritabanına bağlıyız? (Teşhis için; kimlik bilgisi sızdırmaz.)"""
        if self.database_url.startswith("postgresql"):
            return "PostgreSQL"
        if self.is_sqlite:
            return "SQLite"
        return self.database_url.split("://", 1)[0] or "bilinmiyor"

    @property
    def database_is_persistent(self) -> bool:
        """Kayıtlar yeniden dağıtımdan sağ çıkar mı?

        Railway'de SQLite kullanılıyorsa veriler kapsayıcıyla birlikte
        silinir; kalıcılık için Postgres ya da bağlı bir Volume gerekir.
        """
        return not self.is_sqlite


class Selectors(BaseModel):
    """Bir kaynağın CSS seçicileri.

    Değerlerde ``@attr`` soneki niteliği okur: ``"a@href"``, ``"time@datetime"``.
    Virgülle birden fazla aday seçici verilebilir; ilk eşleşen kullanılır.
    """

    item: str = ""
    title: str = ""
    url: str = "a@href"
    date: str = ""
    time: str = ""
    venue: str = ""
    price: str = ""
    category: str = ""
    image: str = "img@src, img@data-src"
    description: str = ""


class Pagination(BaseModel):
    """Sayfalama ayarı: ``?page=2`` gibi."""

    param: str = "page"
    start: int = 1
    max_pages: int = 1
    # Alternatif: URL şablonu ("https://site/etkinlikler/sayfa/{page}")
    template: str | None = None


Strategy = Literal["jsonld", "nextdata", "selectors", "heuristic"]
DEFAULT_STRATEGIES: tuple[Strategy, ...] = ("jsonld", "nextdata", "selectors", "heuristic")


class SourceConfig(BaseModel):
    """Tek bir etkinlik kaynağının tanımı."""

    key: str
    name: str
    enabled: bool = True
    base_url: str
    listing_urls: list[str] = Field(default_factory=list)
    strategies: list[Strategy] = Field(default_factory=lambda: list(DEFAULT_STRATEGIES))
    selectors: Selectors = Field(default_factory=Selectors)
    pagination: Pagination | None = None
    # Belediye/müze kaynaklarında fiyat yoksa ücretsiz varsay.
    free_by_default: bool = False
    # Bu kaynak İzmir'e özel değilse başlık/adres İzmir filtresinden geçsin
    # (İzmir ipucu ARANIR; ipucu yoksa kayıt elenir).
    city_filter: bool = False
    # Açıkça başka bir şehre ait kayıtları ele (İstanbul, Ankara, semt adları…).
    # Şehri belirsiz kayıtlar korunur; bu yüzden varsayılan olarak açık.
    exclude_other_cities: bool = True
    # Birleştirmede hangi kaynağın başlığı/görseli tercih edilsin (büyük = öncelikli).
    priority: int = 50
    # Sadece bu kategorileri içerdiği bilinen kaynak (opsiyonel ipucu).
    default_category: str | None = None
    notes: str = ""
    headers: dict[str, str] = Field(default_factory=dict)

    @property
    def urls(self) -> list[str]:
        return self.listing_urls or [self.base_url]


class SourcesFile(BaseModel):
    sources: list[SourceConfig]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def load_sources(path: str | Path | None = None) -> list[SourceConfig]:
    """``sources.yaml`` dosyasını okur ve doğrular."""
    path = Path(path or get_settings().sources_file)
    if not path.is_file():
        raise FileNotFoundError(
            f"Kaynak dosyası bulunamadı: {path}\n"
            "SOURCES_FILE ortam değişkeniyle konumunu belirtebilirsiniz."
        )
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    parsed = SourcesFile.model_validate(data)
    return parsed.sources


def enabled_sources(path: str | Path | None = None) -> list[SourceConfig]:
    """Sadece açık kaynakları, ortam değişkeniyle filtrelenmiş olarak döndürür.

    ``ONLY_SOURCES=bubilet,biletinial`` ile tek kaynak test edilebilir.
    """
    sources = [s for s in load_sources(path) if s.enabled]
    only = os.getenv("ONLY_SOURCES", "").strip()
    if only:
        wanted = {k.strip() for k in only.split(",") if k.strip()}
        sources = [s for s in sources if s.key in wanted]
    return sources
