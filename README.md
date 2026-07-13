# Dota 2 Esports Data Scraper

Async Python 3.11+ scraper for **Dota 2 esports data** from:

- [Dotabuff](https://www.dotabuff.com) (browser-rendered esports pages)
- [Liquipedia](https://liquipedia.net/dota2) (static HTML)
- [OpenDota API](https://docs.opendota.com) (match deep stats)
- [DLTV](https://dltv.org) (live/ranking feeds)

Covers matches, drafts, player stats, rosters, tournaments, earnings, and rankings. Data lands in SQLite and exports to Parquet/CSV.

---

## Install

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -e ".[dev]"
```

CloakBrowser downloads its Chromium binary on first launch for JS-heavy sources.

```bash
cp .env.example .env
# Set DOTA2_USER_AGENT to a real contact email before heavy runs.
```

## Usage

```bash
dota2-scraper scrape liquipedia
dota2-scraper scrape opendota
dota2-scraper scrape dotabuff
dota2-scraper scrape dltv
dota2-scraper scrape all
dota2-scraper export
dota2-scraper status
```

Limit pages per run:

```bash
dota2-scraper scrape liquipedia --max-pages 50
```

## Configuration

Settings use the `DOTA2_` prefix (see `.env.example`).

| Variable | Default | Purpose |
|----------|---------|---------|
| `DOTA2_DB_PATH` | `dota2.db` | SQLite path |
| `DOTA2_MAX_PAGES_PER_RUN` | `100` | Safety cap |
| `DOTA2_USER_AGENT` | research bot string | Identify yourself |

## Testing

```bash
pytest -q
```

## Responsible use

- Prefer OpenDota/Liquipedia with polite delays.
- Keep Dotabuff concurrency low (default 2).
- Users must comply with each source's Terms of Service.
- Not affiliated with Dotabuff, Liquipedia, OpenDota, or DLTV.

## License

MIT © 2026 ark-daemon — see [LICENSE](LICENSE).
