# Data Scrapers — Structural Data Extraction Architecture

A modular, async-first data-engineering toolkit for high-fidelity DOM parsing, resilient structural scraping, and normalized downstream pipelines. Built for scenarios where target layouts shift, anti-bot scripts run, and data fidelity must remain constant across large-scale ingestion jobs.

## Architecture Highlights

- **Automated DOM Parsing & Structural Data Extraction**  
  Deeply-nested HTML is decomposed into strongly-typed relational rows via defensive, CSS-selectored parsers. Each parser is built to survive missing nodes, renamed classes, and responsive-template duplication without crashing the pipeline.

- **Dynamic Error Handling for Anti-Bot & Layout Shifts**  
  Every fetcher implements exponential-backoff retry with jitter, adaptive rate-limiting, and graceful degradation. A stealth-capable headless browser abstraction handles JavaScript-heavy targets, while static endpoints are served through high-performance `httpx` with HTTP/2 and connection reuse.

- **Efficient JSON / CSV / Parquet Data Pipeline Output**  
  All extracted data lands in a normalized SQLite schema with full foreign-key relationships. A built-in export layer transforms every table into sorted Parquet (or CSV-compatible) files for direct ingestion into BI tools, Pandas, or cloud data warehouses.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Runtime | Python 3.11+ (async-first) |
| Settings | Pydantic Settings (env-driven, `DOTA2_` prefix) |
| HTTP | `httpx` with HTTP/2, custom headers, and backoff |
| Browser | Stealth headless abstraction (Chromium-based) |
| HTML Parsing | `selectolax` + `BeautifulSoup` (defensive dual-path) |
| Database | SQLite + `aiosqlite` (WAL mode, async-safe) |
| Export | `pandas` → `pyarrow` Parquet |
| CLI | `typer` + `rich` |

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

> The stealth browser engine auto-downloads its Chromium binary on first launch; no manual browser installation is required.

## Configuration

Copy the example environment file and adjust to your targets:

```powershell
copy .env.example .env
```

All settings use the `DOTA2_` prefix. Key variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DOTA2_DB_PATH` | `dota2.db` | Local SQLite database |
| `DOTA2_EXPORT_DIR` | `exports` | Parquet output folder |
| `DOTA2_LOG_DIR` | `logs` | Application logs |
| `DOTA2_MAX_PAGES_PER_RUN` | `100` | Safety limit per scraping run |
| `DOTA2_BROWSER_FINGERPRINT_SEED` | `42069` | Fixed entropy seed for deterministic browser fingerprinting |
| `DOTA2_USER_AGENT` | `Dota2EsportsResearchBot/0.1 ...` | Request identity string |

## CLI Usage

Run individual extraction endpoints or the full pipeline:

```powershell
# DOM-driven extraction (JavaScript-heavy targets)
dota2-scraper scrape dotabuff

# Static HTML structural extraction
dota2-scraper scrape liquipedia

# REST API deep-stat ingestion
dota2-scraper scrape opendota

# Live feed & ranking snapshots
dota2-scraper scrape dltv

# Run all configured sources concurrently
dota2-scraper scrape all

# Historical backfill (bounded by year or all-time)
dota2-scraper scrape backfill --year 2023
dota2-scraper scrape backfill --all-time

# Export SQLite tables to sorted Parquet files
dota2-scraper export

# Check database row counts
dota2-scraper status
```

Override limits on the fly:

```powershell
dota2-scraper scrape dotabuff --max-pages 500
dota2-scraper scrape all --max-pages 1000
```

## Database Schema

The normalized schema covers:

- **teams**, **players**, **rosters**, **staff**, **transfers**
- **tournaments**, **matches**, **games**
- **drafts**, **draft_picks**, **player_game_stats**, **game_timelines**
- **earnings**, **rankings**, **objectives**

Every table stores a `raw_json` audit column so upstream parser improvements can be replayed without re-fetching source HTML.

## Design Principles

1. **Defensive Parsing** — Missing fields become `NULL`; malformed rows are logged and skipped.
2. **Resumability** — The pipeline checks existing primary keys before insertion, making long backfills safely interruptible.
3. **Rate Discipline** — Each source has independent concurrency and delay knobs to avoid overloading target infrastructure.
4. **Zero Hardcoded Secrets** — URLs, fingerprints, and identity strings are injected exclusively through the environment (`.env`).

## License

MIT — see `pyproject.toml` for full metadata.
