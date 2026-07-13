# `dota2-scraper`

Async Dota 2 esports scraper (<span style="font-weight: bold">Dotabuff</span>, <span style="font-weight: bold">Liquipedia</span>, <span style="font-weight: bold">OpenDota</span>, <span style="font-weight: bold">DLTV</span>).

**Usage**:

```console
$ dota2-scraper [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `export`: Export every SQLite table to Parquet.
* `status`: Show row counts for the local scraper...
* `scrape`: Run scraping jobs against one or more...

## `dota2-scraper export`

Export every SQLite table to Parquet.

**Usage**:

```console
$ dota2-scraper export [OPTIONS]
```

**Options**:

* `-o, --output-dir PATH`: Parquet output directory.
* `--help`: Show this message and exit.

## `dota2-scraper status`

Show row counts for the local scraper database.

**Usage**:

```console
$ dota2-scraper status [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `dota2-scraper scrape`

Run scraping jobs against one or more sources.

**Usage**:

```console
$ dota2-scraper scrape [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `dotabuff`: Scrape Dotabuff esports pages via...
* `liquipedia`: Scrape Liquipedia tournament, team, and...
* `all`: Scrape Dotabuff and Liquipedia concurrently.
* `backfill`: Run a historical backfill for a year or...
* `opendota`: Ingest deep match stats from the OpenDota...
* `dltv`: Fetch DLTV live matches, rankings, and...

### `dota2-scraper scrape dotabuff`

Scrape Dotabuff esports pages via CloakBrowser DOM extraction.

**Usage**:

```console
$ dota2-scraper scrape dotabuff [OPTIONS]
```

**Options**:

* `-u, --url TEXT`: Dotabuff seed URL.
* `--max-pages INTEGER`: Maximum pages for this run.
* `--help`: Show this message and exit.

### `dota2-scraper scrape liquipedia`

Scrape Liquipedia tournament, team, and roster HTML.

**Usage**:

```console
$ dota2-scraper scrape liquipedia [OPTIONS]
```

**Options**:

* `-u, --url TEXT`: Liquipedia seed URL.
* `--max-pages INTEGER`: Maximum pages for this run.
* `--help`: Show this message and exit.

### `dota2-scraper scrape all`

Scrape Dotabuff and Liquipedia concurrently.

**Usage**:

```console
$ dota2-scraper scrape all [OPTIONS]
```

**Options**:

* `--dotabuff-url TEXT`: Dotabuff seed URL (repeatable).
* `--liquipedia-url TEXT`: Liquipedia seed URL (repeatable).
* `--max-pages INTEGER`: Maximum pages per source for this run.
* `--help`: Show this message and exit.

### `dota2-scraper scrape backfill`

Run a historical backfill for a year or all-time.

**Usage**:

```console
$ dota2-scraper scrape backfill [OPTIONS]
```

**Options**:

* `--year INTEGER`: Year to backfill (e.g. 2023).
* `--all-time`: Backfill all historical data.
* `--source TEXT`: Source: liquipedia, opendota, dotabuff, or all.  [default: all]
* `--help`: Show this message and exit.

### `dota2-scraper scrape opendota`

Ingest deep match stats from the OpenDota REST API.

**Usage**:

```console
$ dota2-scraper scrape opendota [OPTIONS]
```

**Options**:

* `--match-id INTEGER`: Single OpenDota match ID.
* `--since [%Y-%m-%d|%Y-%m-%dT%H:%M:%S|%Y-%m-%d %H:%M:%S]`: Backfill from UTC date (YYYY-MM-DD).
* `--help`: Show this message and exit.

### `dota2-scraper scrape dltv`

Fetch DLTV live matches, rankings, and related feeds.

**Usage**:

```console
$ dota2-scraper scrape dltv [OPTIONS]
```

**Options**:

* `--live`: Fetch only live/recent matches.
* `--rankings`: Fetch only ranking snapshots.
* `--help`: Show this message and exit.
