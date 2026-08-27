"""Komut satırı arayüzü."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import enabled_sources, get_settings, load_sources
from .logging_setup import setup_logging
from .util.dates import Bucket, today

app = typer.Typer(
    add_completion=False,
    help="İzmir etkinlik takip botu — toplama, tekilleştirme ve Telegram sunumu.",
)
console = Console()


def _setup(json_logs: bool = False) -> None:
    settings = get_settings()
    setup_logging(settings.log_level, json_logs=json_logs)


@app.command("serve")
def serve(
    json_logs: bool = typer.Option(False, "--json-logs", help="Makine-okur log çıktısı"),
) -> None:
    """Telegram botunu çalıştırır (Railway'de ana komut budur)."""
    _setup(json_logs)
    from .bot.app import run

    run()


@app.command("scrape")
def scrape(
    store: bool = typer.Option(True, "--store/--no-store", help="Sonuçları veritabanına yaz"),
    cache: bool = typer.Option(True, "--cache/--no-cache", help="HTTP önbelleğini kullan"),
    source: list[str] = typer.Option(None, "--source", "-s", help="Sadece bu kaynakları tara"),
    limit: int = typer.Option(25, "--limit", "-n", help="Ekrana yazılacak etkinlik sayısı"),
) -> None:
    """Tüm kaynakları tarar, tekilleştirir ve özet rapor basar."""
    _setup()
    from .pipeline import collect, collect_and_store
    from .store.db import init_db

    sources = enabled_sources()
    if source:
        wanted = set(source)
        sources = [s for s in sources if s.key in wanted]
        if not sources:
            console.print(f"[red]Eşleşen kaynak yok:[/red] {', '.join(source)}")
            raise typer.Exit(1)

    async def _run():
        if store:
            await init_db()
            return await collect_and_store(sources=sources, use_cache=cache)
        return await collect(sources=sources, use_cache=cache)

    result = asyncio.run(_run())
    console.print(result.report())

    if result.events:
        table = Table(title=f"İlk {min(limit, len(result.events))} etkinlik", show_lines=False)
        table.add_column("Tarih", style="cyan", no_wrap=True)
        table.add_column("Etkinlik", style="bold")
        table.add_column("Mekan", style="dim")
        table.add_column("Fiyat", style="green")
        table.add_column("Kaynak", style="magenta")
        for event in result.events[:limit]:
            table.add_row(
                event.date_label()[:26],
                event.title[:46],
                (event.venue or "-")[:26],
                event.price.label(),
                ",".join(sorted(event.sources))[:28],
            )
        console.print(table)


@app.command("doctor")
def doctor(
    source: str = typer.Option(..., "--source", "-s", help="Kaynak anahtarı (sources.yaml)"),
    save_html: bool = typer.Option(False, "--save-html", help="İndirilen HTML'i diske yaz"),
    url: str = typer.Option(None, "--url", help="Kaynağın URL'i yerine bu adresi dene"),
    show: int = typer.Option(5, "--show", help="Gösterilecek örnek kayıt sayısı"),
) -> None:
    """Bir kaynağı teşhis eder: hangi strateji çalışıyor, ne çıkarıyor.

    Site tasarımını değiştirdiğinde seçicileri kalibre etmek için bu komutu
    kullanın; hangi stratejinin kaç kayıt bulduğunu tek tek raporlar.
    """
    _setup()
    from .models import build_event
    from .scrape.extractors import (
        extract_heuristic,
        extract_jsonld,
        extract_nextdata,
        extract_selectors,
    )
    from .scrape.http import HttpClient

    configs = {s.key: s for s in load_sources()}
    if source not in configs:
        console.print(f"[red]Bilinmeyen kaynak:[/red] {source}")
        console.print(f"Tanımlı kaynaklar: {', '.join(sorted(configs))}")
        raise typer.Exit(1)
    config = configs[source]
    target = url or config.urls[0]

    async def _fetch() -> str:
        settings = get_settings()
        async with HttpClient(
            user_agent=settings.user_agent,
            timeout=settings.http_timeout,
            respect_robots=settings.respect_robots,
            cache_dir=None,
        ) as client:
            result = await client.fetch(target, use_cache=False)
            if not result.ok:
                console.print(f"[red]İndirilemedi:[/red] {result.error} (HTTP {result.status})")
                raise typer.Exit(1)
            return result.text

    html = asyncio.run(_fetch())
    console.print(f"[bold]Kaynak:[/bold] {config.name}")
    console.print(f"[bold]URL:[/bold] {target}  ([green]{len(html):,} bayt[/green])\n")

    if save_html:
        out = Path(f"{source}.html")
        out.write_text(html, encoding="utf-8")
        console.print(f"HTML kaydedildi: [cyan]{out}[/cyan]\n")

    strategies = {
        "jsonld": lambda: extract_jsonld(html, target, source),
        "nextdata": lambda: extract_nextdata(html, target, source),
        "selectors": lambda: extract_selectors(html, target, source, config.selectors),
        "heuristic": lambda: extract_heuristic(html, target, source),
    }

    table = Table(title="Strateji sonuçları")
    table.add_column("Strateji", style="bold")
    table.add_column("Ham kayıt", justify="right")
    table.add_column("Tarihi ayrıştırılan", justify="right", style="green")
    table.add_column("Not", style="dim")

    best: tuple[str, list] = ("", [])
    for name, fn in strategies.items():
        try:
            raws = fn()
        except Exception as exc:
            table.add_row(name, "-", "-", f"hata: {exc}")
            continue
        parsed = [r for r in raws if build_event(r, ref=today()) is not None]
        note = "aktif" if name in config.strategies else "kapalı (sources.yaml)"
        table.add_row(name, str(len(raws)), str(len(parsed)), note)
        if len(parsed) > len(best[1]):
            best = (name, parsed)
    console.print(table)

    if not best[1]:
        console.print(
            "\n[yellow]Hiçbir strateji tarihli etkinlik çıkaramadı.[/yellow]\n"
            "Yapılacaklar:\n"
            "  1. --save-html ile HTML'i kaydedip yapısına bakın.\n"
            "  2. sources.yaml içindeki 'item' ve 'title' seçicilerini güncelleyin.\n"
            "  3. Sayfa JavaScript ile yükleniyorsa siteye ait bir API/JSON uç noktası arayın."
        )
        raise typer.Exit(2)

    console.print(f"\n[bold green]En iyi strateji: {best[0]}[/bold green] — örnek kayıtlar:\n")
    for raw in best[1][:show]:
        event = build_event(raw, free_by_default=config.free_by_default, ref=today())
        assert event is not None
        console.print(f"  • [bold]{event.title[:70]}[/bold]")
        console.print(
            f"    {event.date_label()} · {event.venue or 'mekan yok'} · "
            f"{event.price.label()} · {event.category.label}"
        )
        console.print(f"    [dim]{raw.url or 'link yok'}[/dim]")


@app.command("sources")
def list_sources() -> None:
    """Tanımlı kaynakları listeler."""
    _setup()
    table = Table(title="Kaynaklar")
    table.add_column("Anahtar", style="cyan")
    table.add_column("Ad")
    table.add_column("Açık", justify="center")
    table.add_column("Öncelik", justify="right")
    table.add_column("Stratejiler", style="dim")
    for source in load_sources():
        table.add_row(
            source.key,
            source.name,
            "✓" if source.enabled else "-",
            str(source.priority),
            ", ".join(source.strategies),
        )
    console.print(table)


@app.command("list")
def list_events(
    bucket: str = typer.Option(
        "bu_hafta",
        "--bucket",
        "-b",
        help="bugun|yarin|bu_hafta|hafta_sonu|gelecek_hafta|bu_ay|ileride",
    ),
    free: bool = typer.Option(False, "--free", help="Sadece ücretsizler"),
    query: str = typer.Option(None, "--ara", "-q", help="Başlık/mekan araması"),
    limit: int = typer.Option(30, "--limit", "-n"),
) -> None:
    """Veritabanındaki etkinlikleri listeler."""
    _setup()
    from .store import repo
    from .store.db import init_db, session_scope

    try:
        selected = Bucket(bucket)
    except ValueError:
        console.print(f"[red]Geçersiz kova:[/red] {bucket}")
        raise typer.Exit(1) from None

    async def _run():
        await init_db()
        async with session_scope() as session:
            if query:
                return await repo.get_events(session, start=today(), query=query, limit=limit)
            return await repo.get_events_for_bucket(session, selected, free_only=free, limit=limit)

    events = asyncio.run(_run())
    if not events:
        console.print("[yellow]Kayıt bulunamadı.[/yellow] Önce 'scrape' çalıştırın.")
        return

    table = Table(title=f"{selected.label} ({len(events)} etkinlik)")
    table.add_column("Tarih", style="cyan", no_wrap=True)
    table.add_column("Etkinlik", style="bold")
    table.add_column("Mekan", style="dim")
    table.add_column("Fiyat", style="green")
    table.add_column("Kaynak", justify="right", style="magenta")
    for event in events:
        table.add_row(
            event.date_label()[:26],
            event.title[:48],
            (event.venue or "-")[:26],
            event.price.label(),
            str(event.source_count),
        )
    console.print(table)


@app.command("temizle")
def purge(
    all_events: bool = typer.Option(
        False, "--hepsi", help="Tüm etkinlik kayıtlarını sil (yeniden taramaya hazırlan)"
    ),
    stale_days: int = typer.Option(
        14, "--bayat-gun", help="Bu kadar gündür görülmeyen kayıtları sil"
    ),
    yes: bool = typer.Option(False, "--evet", "-y", help="Onay sorma"),
) -> None:
    """Bayat veya bozuk etkinlik kayıtlarını siler.

    Ayrıştırma düzeltmelerinden sonra eski kayıtlar gelecek tarihli
    oldukları için kendiliğinden düşmez; bu komut onları temizler.
    """
    _setup()
    from .store import repo
    from .store.db import init_db, session_scope

    if all_events and not yes:
        typer.confirm("Tüm etkinlik kayıtları silinecek. Emin misiniz?", abort=True)

    async def _run() -> int:
        await init_db()
        async with session_scope() as session:
            if all_events:
                return await repo.delete_all_events(session)
            return await repo.prune_stale_events(session, days=stale_days)

    removed = asyncio.run(_run())
    console.print(f"[green]{removed} kayıt silindi.[/green]")
    if removed:
        console.print("Yeniden doldurmak için: [cyan]izmir-etkinlik scrape --no-cache[/cyan]")


@app.command("initdb")
def initdb() -> None:
    """Veritabanı tablolarını oluşturur."""
    _setup()
    from .store.db import init_db

    asyncio.run(init_db())
    console.print("[green]Veritabanı hazır.[/green]")


@app.command("stats")
def stats() -> None:
    """Veritabanı ve kaynak sağlığı özeti."""
    _setup()
    from .store import repo
    from .store.db import init_db, session_scope

    async def _run():
        await init_db()
        async with session_scope() as session:
            return (
                await repo.count_events(session),
                await repo.category_counts(session),
                await repo.source_health(session),
                await repo.last_run(session),
            )

    counts, categories, health, run = asyncio.run(_run())
    settings = get_settings()
    mark = "[green]kalıcı[/green]" if settings.database_is_persistent else "[yellow]geçici[/yellow]"
    console.print(f"[bold]Veritabanı:[/bold] {settings.database_label} ({mark})")
    console.print(
        f"[bold]Gelecek etkinlik:[/bold] {counts['gelecek']} "
        f"([green]{counts['ucretsiz']} ücretsiz[/green], "
        f"{counts['cok_kaynakli']} çok kaynaklı)"
    )
    if run:
        console.print(
            f"[dim]Son tarama: {run.finished_at} · {run.raw_count} -> "
            f"{run.unique_count} · {run.duration_seconds:.0f} sn[/dim]"
        )
    if categories:
        console.print("\n[bold]Kategoriler[/bold]")
        for key, count in categories.items():
            console.print(f"  {key}: {count}")
    if health:
        table = Table(title="Kaynak sağlığı")
        table.add_column("Kaynak", style="cyan")
        table.add_column("Son sayı", justify="right")
        table.add_column("Strateji", style="dim")
        table.add_column("Ard arda hata", justify="right", style="red")
        for row in health:
            table.add_row(
                row.source_key,
                str(row.last_count),
                row.last_strategy or "-",
                str(row.consecutive_failures),
            )
        console.print(table)


def main() -> None:  # pragma: no cover - konsol girişi
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
