"""Nazik (polite) HTTP istemcisi.

Sorumlulukları:
  * host başına eşzamanlılık sınırı ve istekler arası bekleme
  * geçici hatalarda üstel geri çekilmeli yeniden deneme
  * robots.txt kontrolü (kapatılabilir)
  * disk üzerinde kısa ömürlü önbellek (geliştirirken aynı sayfayı
    tekrar tekrar indirmemek için)
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import structlog

log = structlog.get_logger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _ascii_safe(value: str) -> str:
    """HTTP başlık değerini ASCII'ye indirger.

    httpx başlıkları ascii ile kodlar; yapılandırmaya Türkçe karakter
    sızarsa her istek ``UnicodeEncodeError`` ile patlar.
    """
    return value.encode("ascii", "ignore").decode("ascii") or "IzmirEtkinlikBot/1.0"


@dataclass
class FetchResult:
    """Bir sayfa indirme sonucu."""

    url: str
    status: int
    text: str = ""
    from_cache: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300 and bool(self.text)


@dataclass
class HttpClient:
    """Kaynakları indirmek için paylaşılan istemci."""

    user_agent: str = "IzmirEtkinlikBot/1.0"
    timeout: float = 25.0
    max_retries: int = 3
    concurrency: int = 4
    delay_seconds: float = 1.0
    cache_dir: Path | None = None
    cache_ttl_seconds: int = 1800
    respect_robots: bool = True

    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)
    _sem: asyncio.Semaphore | None = field(default=None, init=False, repr=False)
    _host_locks: dict[str, asyncio.Lock] = field(
        default_factory=lambda: defaultdict(asyncio.Lock), init=False, repr=False
    )
    _last_hit: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _robots: dict[str, RobotFileParser | None] = field(default_factory=dict, init=False, repr=False)

    async def __aenter__(self) -> HttpClient:
        headers = {
            "User-Agent": _ascii_safe(self.user_agent),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.6",
            "Cache-Control": "no-cache",
        }
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(self.timeout),
            follow_redirects=True,
            http2=True,
        )
        self._sem = asyncio.Semaphore(self.concurrency)
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # --- önbellek ------------------------------------------------------------

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha1(url.encode()).hexdigest()
        return self.cache_dir / f"{digest}.html"

    def _read_cache(self, url: str) -> str | None:
        path = self._cache_path(url)
        if not path or not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.cache_ttl_seconds:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _write_cache(self, url: str, text: str) -> None:
        path = self._cache_path(url)
        if path:
            try:
                path.write_text(text, encoding="utf-8")
            except OSError as exc:
                log.debug("cache_write_failed", url=url, error=str(exc))

    # --- robots.txt ----------------------------------------------------------

    async def _robots_allows(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            self._robots[origin] = await self._load_robots(origin)
        parser = self._robots[origin]
        if parser is None:  # robots.txt okunamadıysa engelleme
            return True
        return parser.can_fetch(self.user_agent, url)

    async def _load_robots(self, origin: str) -> RobotFileParser | None:
        assert self._client is not None
        try:
            resp = await self._client.get(urljoin(origin, "/robots.txt"), timeout=10.0)
            if resp.status_code >= 400:
                return None
            parser = RobotFileParser()
            parser.parse(resp.text.splitlines())
            return parser
        except (httpx.HTTPError, UnicodeDecodeError) as exc:
            log.debug("robots_unavailable", origin=origin, error=str(exc))
            return None

    # --- indirme -------------------------------------------------------------

    async def _throttle(self, host: str) -> None:
        """Aynı host'a ardışık istekler arasında en az ``delay_seconds`` bırak."""
        async with self._host_locks[host]:
            last = self._last_hit.get(host)
            if last is not None:
                wait = self.delay_seconds - (time.monotonic() - last)
                if wait > 0:
                    await asyncio.sleep(wait)
            self._last_hit[host] = time.monotonic()

    async def fetch(
        self, url: str, *, extra_headers: dict[str, str] | None = None, use_cache: bool = True
    ) -> FetchResult:
        """Bir sayfayı indirir; hata durumunda istisna fırlatmaz, sonucu döndürür."""
        if self._client is None or self._sem is None:
            raise RuntimeError("HttpClient 'async with' bloğu içinde kullanılmalı")

        if use_cache:
            cached = self._read_cache(url)
            if cached is not None:
                return FetchResult(url=url, status=200, text=cached, from_cache=True)

        if not await self._robots_allows(url):
            log.warning("robots_disallowed", url=url)
            return FetchResult(url=url, status=0, error="robots.txt izin vermiyor")

        host = urlparse(url).netloc
        last_error: str | None = None
        status = 0

        for attempt in range(1, self.max_retries + 1):
            async with self._sem:
                await self._throttle(host)
                try:
                    safe_headers = (
                        {k: _ascii_safe(v) for k, v in extra_headers.items()}
                        if extra_headers
                        else None
                    )
                    resp = await self._client.get(url, headers=safe_headers)
                    status = resp.status_code
                    if status in RETRYABLE_STATUS:
                        last_error = f"HTTP {status}"
                    elif status >= 400:
                        return FetchResult(url=url, status=status, error=f"HTTP {status}")
                    else:
                        text = resp.text
                        if use_cache:
                            self._write_cache(url, text)
                        return FetchResult(url=url, status=status, text=text)
                except httpx.HTTPError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"

            if attempt < self.max_retries:
                backoff = min(2**attempt, 16)
                log.debug("retry", url=url, attempt=attempt, wait=backoff, error=last_error)
                await asyncio.sleep(backoff)

        log.warning("fetch_failed", url=url, error=last_error)
        return FetchResult(url=url, status=status, error=last_error or "bilinmeyen hata")

    async def fetch_all(self, urls: list[str], **kwargs: object) -> list[FetchResult]:
        """Birden fazla sayfayı eşzamanlı indirir."""
        tasks = [self.fetch(u, **kwargs) for u in urls]  # type: ignore[arg-type]
        return list(await asyncio.gather(*tasks))


def absolutize(base: str, href: str | None) -> str | None:
    """Göreli linkleri mutlak URL'e çevirir."""
    if not href:
        return None
    href = href.strip()
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    return urljoin(base, href)
