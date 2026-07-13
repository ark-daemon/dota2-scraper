from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from dota2_scraper.cli_ui import (
    configure_rich_logging,
    console,
    end_summary_table,
    scrape_progress,
    startup_panel,
    status_table,
    timed_run,
)
from dota2_scraper.config import Settings, get_settings
from dota2_scraper.pipeline import ScrapePipeline
from dota2_scraper.storage.database import Database, export_tables

app = typer.Typer(
    name="dota2-scraper",
    help="Async Dota 2 esports scraper ([bold]Dotabuff[/], [bold]Liquipedia[/], [bold]OpenDota[/], [bold]DLTV[/]).",
    no_args_is_help=True,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)
scrape_app = typer.Typer(
    help="Run scraping jobs against one or more sources.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(scrape_app, name="scrape")


def _settings(max_pages: int | None = None) -> Settings:
    base = get_settings()
    if max_pages is None:
        return base
    data = base.model_dump()
    data["max_pages_per_run"] = max_pages
    return Settings(**data)


def _database(settings: Settings) -> Database:
    return Database(settings.db_path, settings.schema_path)


def _boot(settings: Settings, *, target: str, extra: dict[str, Any] | None = None) -> None:
    configure_rich_logging("INFO", settings.log_dir / "scraper.log")
    rows = {
        "Target": target,
        "DB path": settings.db_path,
        "Export dir": settings.export_dir,
        "Max pages/run": settings.max_pages_per_run,
        "User-Agent": settings.user_agent[:60] + ("…" if len(settings.user_agent) > 60 else ""),
    }
    if extra:
        rows.update(extra)
    startup_panel(title="dota2-scraper · run config", rows=rows)


def _summarize(result: dict[str, Any], duration_s: float, outputs: list[Path] | None = None) -> None:
    flat: list[tuple[str, Any]] = []
    errors = 0
    for key, value in result.items():
        if isinstance(value, dict):
            flat.append((str(key), ", ".join(f"{k}={v}" for k, v in value.items()) or "none"))
        else:
            flat.append((str(key), value))
            if "error" in str(key).lower():
                try:
                    errors += int(value)
                except (TypeError, ValueError):
                    pass
    end_summary_table(
        title="Scrape summary",
        rows=flat,
        outputs=outputs,
        duration_s=duration_s,
    )


def _run_with_progress(label: str, coro_factory) -> dict[str, Any]:
    with timed_run() as elapsed, scrape_progress() as progress:
        task = progress.add_task(label, total=None)
        result = asyncio.run(coro_factory())
        progress.update(task, description=f"{label} · done")
    _summarize(result if isinstance(result, dict) else {"result": result}, elapsed[0])
    return result if isinstance(result, dict) else {"result": result}


@scrape_app.command("dotabuff")
def scrape_dotabuff(
    url: Annotated[list[str] | None, typer.Option("--url", "-u", help="Dotabuff seed URL.")] = None,
    max_pages: Annotated[int | None, typer.Option(help="Maximum pages for this run.")] = None,
) -> None:
    """Scrape Dotabuff esports pages via CloakBrowser DOM extraction."""
    settings = _settings(max_pages)
    _boot(
        settings,
        target="dotabuff",
        extra={"Delay (s)": settings.dotabuff_delay_seconds, "Concurrency": settings.dotabuff_concurrency},
    )
    pipeline = ScrapePipeline(settings, _database(settings))
    _run_with_progress("dotabuff", lambda: pipeline.scrape_dotabuff(url))


@scrape_app.command("liquipedia")
def scrape_liquipedia(
    url: Annotated[list[str] | None, typer.Option("--url", "-u", help="Liquipedia seed URL.")] = None,
    max_pages: Annotated[int | None, typer.Option(help="Maximum pages for this run.")] = None,
) -> None:
    """Scrape Liquipedia tournament, team, and roster HTML."""
    settings = _settings(max_pages)
    _boot(
        settings,
        target="liquipedia",
        extra={"Delay (s)": settings.liquipedia_delay_seconds, "Concurrency": settings.liquipedia_concurrency},
    )
    pipeline = ScrapePipeline(settings, _database(settings))
    _run_with_progress("liquipedia", lambda: pipeline.scrape_liquipedia(url))


@scrape_app.command("all")
def scrape_all(
    dotabuff_url: Annotated[
        list[str] | None,
        typer.Option("--dotabuff-url", help="Dotabuff seed URL (repeatable)."),
    ] = None,
    liquipedia_url: Annotated[
        list[str] | None,
        typer.Option("--liquipedia-url", help="Liquipedia seed URL (repeatable)."),
    ] = None,
    max_pages: Annotated[int | None, typer.Option(help="Maximum pages per source for this run.")] = None,
) -> None:
    """Scrape Dotabuff and Liquipedia concurrently."""
    settings = _settings(max_pages)
    _boot(settings, target="dotabuff + liquipedia")
    pipeline = ScrapePipeline(settings, _database(settings))
    with timed_run() as elapsed, scrape_progress() as progress:
        task = progress.add_task("dotabuff + liquipedia", total=None)
        result = asyncio.run(pipeline.scrape_all(dotabuff_url, liquipedia_url))
        progress.update(task, description="dotabuff + liquipedia · done")
    rows = []
    for source, payload in (result or {}).items():
        if isinstance(payload, dict):
            rows.append((source, ", ".join(f"{k}={v}" for k, v in payload.items()) or "none"))
        else:
            rows.append((source, payload))
    end_summary_table(title="Scrape summary", rows=rows, duration_s=elapsed[0])


@scrape_app.command("backfill")
def scrape_backfill(
    year: Annotated[int | None, typer.Option("--year", help="Year to backfill (e.g. 2023).")] = None,
    all_time: Annotated[bool, typer.Option("--all-time", help="Backfill all historical data.")] = False,
    source: Annotated[
        str,
        typer.Option("--source", help="Source: liquipedia, opendota, dotabuff, or all."),
    ] = "all",
) -> None:
    """Run a historical backfill for a year or all-time."""
    if not year and not all_time:
        console.print("[red]Error:[/] must specify either --year or --all-time")
        raise typer.Exit(1)

    settings = _settings(max_pages=500000 if all_time else 5000)
    _boot(
        settings,
        target=f"backfill:{source}",
        extra={"Year": year or "all-time", "Max pages": settings.max_pages_per_run},
    )
    pipeline = ScrapePipeline(settings, _database(settings))
    _run_with_progress(
        f"backfill {source}",
        lambda: pipeline.scrape_backfill(year, all_time, source),
    )


@scrape_app.command("opendota")
def scrape_opendota(
    match_id: Annotated[int | None, typer.Option("--match-id", help="Single OpenDota match ID.")] = None,
    since: Annotated[
        datetime | None,
        typer.Option("--since", help="Backfill from UTC date (YYYY-MM-DD)."),
    ] = None,
) -> None:
    """Ingest deep match stats from the OpenDota REST API."""
    settings = _settings()
    _boot(
        settings,
        target="opendota",
        extra={"Match ID": match_id or "batch", "Since": since.date() if since else "default"},
    )
    pipeline = ScrapePipeline(settings, _database(settings))
    _run_with_progress(
        "opendota",
        lambda: pipeline.scrape_opendota(match_id=match_id, since=since.date() if since else None),
    )


@scrape_app.command("dltv")
def scrape_dltv(
    live: Annotated[bool, typer.Option("--live", help="Fetch only live/recent matches.")] = False,
    rankings: Annotated[bool, typer.Option("--rankings", help="Fetch only ranking snapshots.")] = False,
) -> None:
    """Fetch DLTV live matches, rankings, and related feeds."""
    settings = _settings()
    _boot(
        settings,
        target="dltv",
        extra={"Live only": live, "Rankings only": rankings, "Delay (s)": settings.dltv_delay_seconds},
    )
    pipeline = ScrapePipeline(settings, _database(settings))
    _run_with_progress(
        "dltv",
        lambda: pipeline.scrape_dltv(live_only=live, rankings_only=rankings),
    )


@app.command("export")
def export(
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Parquet output directory."),
    ] = None,
) -> None:
    """Export every SQLite table to Parquet."""
    settings = get_settings()
    configure_rich_logging("INFO", settings.log_dir / "scraper.log")
    database = _database(settings)
    asyncio.run(database.init())
    target = output_dir or settings.export_dir
    startup_panel(
        title="dota2-scraper · export",
        rows={"DB path": settings.db_path, "Output format": "parquet", "Export dir": target},
    )
    with timed_run() as elapsed:
        written = export_tables(settings.db_path, target)
    end_summary_table(
        title="Export summary",
        rows=[("Tables", len(written))],
        outputs=list(written.values()),
        duration_s=elapsed[0],
    )


@app.command("status")
def status() -> None:
    """Show row counts for the local scraper database."""
    settings = get_settings()
    configure_rich_logging("INFO", settings.log_dir / "scraper.log")
    database = _database(settings)
    asyncio.run(database.init())
    counts = asyncio.run(database.table_counts())
    status_table(f"Database · {settings.db_path}", counts)


if __name__ == "__main__":
    app()
