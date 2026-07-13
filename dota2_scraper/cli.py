from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from dota2_scraper.config import Settings, get_settings
from dota2_scraper.logging_config import configure_logging
from dota2_scraper.pipeline import ScrapePipeline
from dota2_scraper.storage.database import Database, export_tables

console = Console()
app = typer.Typer(
    name="dota2-scraper",
    help="Async Dota 2 esports scraper (Dotabuff, Liquipedia, OpenDota, DLTV).",
    no_args_is_help=True,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)
scrape_app = typer.Typer(help="Run scraping jobs.", no_args_is_help=True)
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


def _render_result(result: dict) -> None:
    table = Table(title="Scrape Result")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    for key, value in result.items():
        if isinstance(value, dict):
            value = ", ".join(f"{k}={v}" for k, v in value.items()) or "none"
        table.add_row(str(key), str(value))
    console.print(table)


@scrape_app.command("dotabuff")
def scrape_dotabuff(
    url: Annotated[list[str] | None, typer.Option("--url", "-u", help="Dotabuff seed URL.")] = None,
    max_pages: Annotated[int | None, typer.Option(help="Maximum pages for this run.")] = None,
) -> None:
    """Scrape esports match pages via DOM-driven extraction."""
    settings = _settings(max_pages)
    configure_logging(settings.log_dir)
    pipeline = ScrapePipeline(settings, _database(settings))
    result = asyncio.run(pipeline.scrape_dotabuff(url))
    _render_result(result)


@scrape_app.command("liquipedia")
def scrape_liquipedia(
    url: Annotated[list[str] | None, typer.Option("--url", "-u", help="Liquipedia seed URL.")] = None,
    max_pages: Annotated[int | None, typer.Option(help="Maximum pages for this run.")] = None,
) -> None:
    """Scrape tournament and roster data from static HTML endpoints."""
    settings = _settings(max_pages)
    configure_logging(settings.log_dir)
    pipeline = ScrapePipeline(settings, _database(settings))
    result = asyncio.run(pipeline.scrape_liquipedia(url))
    _render_result(result)


@scrape_app.command("all")
def scrape_all(
    dotabuff_url: Annotated[
        list[str] | None,
        typer.Option("--dotabuff-url", help="Dotabuff seed URL. Can be repeated."),
    ] = None,
    liquipedia_url: Annotated[
        list[str] | None,
        typer.Option("--liquipedia-url", help="Liquipedia seed URL. Can be repeated."),
    ] = None,
    max_pages: Annotated[int | None, typer.Option(help="Maximum pages per source for this run.")] = None,
) -> None:
    """Scrape Dotabuff and Liquipedia concurrently."""
    settings = _settings(max_pages)
    configure_logging(settings.log_dir)
    pipeline = ScrapePipeline(settings, _database(settings))
    result = asyncio.run(pipeline.scrape_all(dotabuff_url, liquipedia_url))
    console.print(result)


@scrape_app.command("backfill")
def scrape_backfill(
    year: Annotated[int | None, typer.Option("--year", help="Year to backfill (e.g. 2023).")] = None,
    all_time: Annotated[bool, typer.Option("--all-time", help="Backfill all historical data.")] = False,
    source: Annotated[str, typer.Option("--source", help="Source to backfill (liquipedia, opendota, dotabuff, or all).")] = "all",
) -> None:
    """Run a historical backfill for a specific year or all-time."""
    if not year and not all_time:
        console.print("[red]Error: Must specify either --year or --all-time[/red]")
        raise typer.Exit(1)

    # Use a massive limit for all-time runs, normal limit for yearly
    settings = _settings(max_pages=500000 if all_time else 5000)
    configure_logging(settings.log_dir)
    pipeline = ScrapePipeline(settings, _database(settings))
    result = asyncio.run(pipeline.scrape_backfill(year, all_time, source))
    _render_result(result)


@scrape_app.command("opendota")
def scrape_opendota(
    match_id: Annotated[int | None, typer.Option("--match-id", help="Single OpenDota match ID to ingest.")] = None,
    since: Annotated[
        datetime | None,
        typer.Option("--since", help="Backfill from UTC date (YYYY-MM-DD)."),
    ] = None,
) -> None:
    """Ingest deep statistical match data via REST API."""
    settings = _settings()
    configure_logging(settings.log_dir)
    pipeline = ScrapePipeline(settings, _database(settings))
    result = asyncio.run(pipeline.scrape_opendota(match_id=match_id, since=since.date() if since else None))
    _render_result(result)


@scrape_app.command("dltv")
def scrape_dltv(
    live: Annotated[bool, typer.Option("--live", help="Fetch only live/recent DLTV matches.")] = False,
    rankings: Annotated[bool, typer.Option("--rankings", help="Fetch only DLTV ranking snapshots.")] = False,
) -> None:
    """Fetch live match updates, rankings, and transfer feeds."""
    settings = _settings()
    configure_logging(settings.log_dir)
    pipeline = ScrapePipeline(settings, _database(settings))
    result = asyncio.run(pipeline.scrape_dltv(live_only=live, rankings_only=rankings))
    _render_result(result)


@app.command("export")
def export(
    output_dir: Annotated[Path | None, typer.Option("--output-dir", "-o", help="Parquet output directory.")] = None,
) -> None:
    """Export every SQLite table to Parquet."""
    settings = get_settings()
    configure_logging(settings.log_dir)
    database = _database(settings)
    asyncio.run(database.init())
    target = output_dir or settings.export_dir
    written = export_tables(settings.db_path, target)
    table = Table(title="Parquet Exports")
    table.add_column("Table", style="cyan")
    table.add_column("Path", style="green")
    for name, path in written.items():
        table.add_row(name, str(path))
    console.print(table)


@app.command("status")
def status() -> None:
    """Show row counts for the local scraper database."""
    settings = get_settings()
    configure_logging(settings.log_dir)
    database = _database(settings)
    asyncio.run(database.init())
    counts = asyncio.run(database.table_counts())
    table = Table(title=f"Database Status: {settings.db_path}")
    table.add_column("Table", style="cyan")
    table.add_column("Rows", justify="right", style="green")
    for name, count in counts.items():
        table.add_row(name, str(count))
    console.print(table)


if __name__ == "__main__":
    app()
