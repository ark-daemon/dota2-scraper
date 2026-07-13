# dota2-scraper

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-beta-orange.svg)](CHANGELOG.md)

Async multi-source pipeline for **Dota 2 esports data** - Liquipedia + OpenDota API + DLTV over httpx, Dotabuff via CloakBrowser - staged in SQLite and exported to Parquet.

**Fleet:** [vlr-scraper](https://github.com/ark-daemon/vlr-scraper) | [hltv-scraper](https://github.com/ark-daemon/hltv-scraper) | [rocket-league-scraper](https://github.com/ark-daemon/rocket-league-scraper) | [lol-esports-scraper](https://github.com/ark-daemon/lol-esports-scraper)

---

## What it does

Ingests tournament structure, teams, players, series results, per-game drafts/stats, rankings, transfers, and earnings-shaped records into one schema (`source` + `source_id` keys). Prefer **OpenDota** and **Liquipedia** for stable bulk data; use Dotabuff/DLTV when you need those surfaces.

Maturity: **beta (`0.1.0`)**. Multi-source identity is **not** fully reconciled into a single canonical entity graph - rows coexist with source tags. Not affiliated with Dotabuff, Liquipedia, OpenDota, or DLTV.

---

## Architecture

```
                         seeds (per source)
                                |
          +---------------------+---------------------+
          |                     |                     |
          v                     v                     v
   DotabuffFetcher       LiquipediaFetcher    OpenDotaFetcher / DltvFetcher
   CloakBrowser          httpx (+ delay)      httpx (+ rate limit / tenacity)
   fingerprint seed      BS4 / selectolax     JSON -> opendota_parser
          |                     |                     |
          +---------------------+---------------------+
                                |
                                v
                   ScrapePipeline (async queues,
                   max_pages_per_run cap, URL de-dupe)
                                |
                                v
                   storage.Database -> SQLite WAL
                                |
                                v
                   export -> Parquet (pandas + pyarrow)
```

**Resilience vocabulary (precise):**

- **Per-fetcher delays** (`DOTA2_*_DELAY_SECONDS`) - spacing, not a token bucket.
- **tenacity** retries with exponential jitter on network/5xx/429 for HTTP fetchers.
- **No circuit breaker** (that term is reserved for vlr-scraper's global failure trip).
- **CloakBrowser** only on the Dotabuff path (`launch_async` + `--fingerprint=`). Error text mentions `patchright install-deps chromium` if the stealth stack is incomplete.

CLI `scrape all` currently runs **Dotabuff + Liquipedia** concurrently (`pipeline.scrape_all`); OpenDota and DLTV are separate commands.

---

## Quickstart

```bash
git clone https://github.com/ark-daemon/dota2-scraper.git
cd dota2-scraper

python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

pip install -e ".[dev]"
# Dotabuff path needs CloakBrowser; first launch downloads Chromium.
# If launch fails, install browser deps (CloakBrowser/patchright stack), e.g.:
#   patchright install-deps chromium

cp .env.example .env
# set DOTA2_USER_AGENT with a real contact

dota2-scraper --help
dota2-scraper scrape liquipedia --max-pages 20
dota2-scraper scrape opendota
dota2-scraper scrape dltv
dota2-scraper scrape dotabuff --max-pages 10
dota2-scraper status
dota2-scraper export -o exports
```

Historical helper:

```bash
dota2-scraper scrape backfill --year 2024
# or: dota2-scraper scrape backfill --all-time
```

---

## Configuration

`pydantic-settings` with prefix **`DOTA2_`** (`dota2_scraper/config.py`).

| Variable | Default | Role |
|----------|---------|------|
| `DOTA2_DB_PATH` | `dota2.db` | SQLite path |
| `DOTA2_EXPORT_DIR` | `exports` | Parquet directory |
| `DOTA2_LOG_DIR` | `logs` | Logs |
| `DOTA2_DOTABUFF_BASE_URL` | `https://www.dotabuff.com` | Origin |
| `DOTA2_LIQUIPEDIA_BASE_URL` | `https://liquipedia.net` | Origin |
| `DOTA2_OPENDOTA_BASE_URL` | `https://api.opendota.com/api` | API root |
| `DOTA2_DLTV_BASE_URL` | `https://dltv.org` | Origin |
| `DOTA2_BROWSER_FINGERPRINT_SEED` | `42069` | CloakBrowser fingerprint arg |
| `DOTA2_DOTABUFF_CONCURRENCY` | `2` (1-5) | Parallel Dotabuff workers |
| `DOTA2_LIQUIPEDIA_CONCURRENCY` | `4` (1-8) | Parallel Liquipedia workers |
| `DOTA2_DLTV_CONCURRENCY` | `1` (1-3) | Parallel DLTV workers |
| `DOTA2_REQUEST_TIMEOUT_SECONDS` | `30` | HTTP timeout |
| `DOTA2_DOTABUFF_DELAY_SECONDS` | `2.5` | Delay between Dotabuff pages |
| `DOTA2_LIQUIPEDIA_DELAY_SECONDS` | `1.5` | Delay between Liquipedia pages |
| `DOTA2_DLTV_DELAY_SECONDS` | `0.5` | Delay between DLTV pages |
| `DOTA2_MAX_PAGES_PER_RUN` | `100` | Hard cap on scheduled URLs per run |
| `DOTA2_USER_AGENT` | research bot placeholder | httpx UA (replace contact) |

Default seeds (overridable only by code/CLI URL flags, not env lists): Dotabuff `/esports`; Liquipedia tournament/team/upcoming portals.

> Note: `.env.example` must not invent knobs that `Settings` ignores.

---

## Data model + sample output

Schema: `dota2_scraper/schemas/schema.sql`.

| Group | Tables |
|-------|--------|
| Entities | `teams`, `players`, `tournaments`, `rosters`, `staff`, `standins`, `transfers`, `earnings` |
| Matches | `matches`, `games`, `drafts`, `draft_picks`, `player_game_stats`, `game_timelines` |
| Extra | `opendota_objectives`, `ept_rankings`, `world_rankings`, `scraper_metadata` |

Most fact tables carry `source`, `source_id`, and often `raw_json` for replay/debug.

**Illustrative export shape** (Parquet columns match SQLite):

```text
tournaments.parquet
  source=liquipedia  source_id=...  name="The International 2025"  region=...  prize_pool_total=...

matches.parquet
  source=liquipedia  team_a_name="Team Spirit"  team_b_name="Team Liquid"
  team_a_score=2  team_b_score=1  series_format="Bo3"  status=...
```

CLI `export` writes **Parquet only** (not CSV/JSON).

---

## Current limitations

- **`max_pages_per_run`** stops crawls early by design; raise deliberately.
- **Cross-source IDs are not merged.** Same team may appear under multiple `source` values.
- **Dotabuff depends on CloakBrowser** and is the most fragile/expensive path.
- **OpenDota public rate limits** apply; no API key plumbing in Settings today.
- **No circuit breaker** - only delays + tenacity retries.
- **Parser coverage varies** by page type/season; missing fields become NULL.
- **Tests** cover packaging smoke, utils, and a Liquipedia HTML fixture - not live multi-source integration.

---

## Tech stack

| Layer | Actually used |
|-------|----------------|
| Runtime | Python >=3.11, asyncio |
| CLI | typer, rich |
| Config | pydantic + pydantic-settings |
| HTTP | httpx (HTTP/2 extra enabled in deps) |
| Browser | cloakbrowser for Dotabuff only |
| HTML | beautifulsoup4 + selectolax |
| Retry | tenacity |
| Storage | aiosqlite |
| Export | pandas + pyarrow -> Parquet |
| Logging | loguru; progress via tqdm / rich CLI chrome |
| Quality | pytest (dev) |

---

## License

MIT (c) ark-daemon - see [LICENSE](LICENSE).

See also [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CHANGELOG.md](CHANGELOG.md).

## Command reference

Full Typer-generated CLI docs: [COMMANDS.md](COMMANDS.md).
