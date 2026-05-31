from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any, Protocol

from loguru import logger
from tqdm import tqdm

from dota2_scraper.config import Settings
from dota2_scraper.fetchers.dltv_fetcher import DltvFetcher
from dota2_scraper.fetchers.dotabuff_fetcher import DotabuffFetcher
from dota2_scraper.fetchers.liquipedia_fetcher import LiquipediaFetcher
from dota2_scraper.fetchers.opendota_fetcher import OpenDotaFetcher
from dota2_scraper.models import FetchJob, FetchedPage, PageKind, ParsedPayload, Source
from dota2_scraper.parsers.dltv_parser import DltvParser
from dota2_scraper.parsers.dotabuff_parser import DotabuffParser
from dota2_scraper.parsers.liquipedia_parser import LiquipediaParser
from dota2_scraper.parsers.opendota_parser import OpenDotaParser
from dota2_scraper.storage.database import Database
from dota2_scraper.utils import now_utc_iso


class Fetcher(Protocol):
    async def fetch(self, job: FetchJob) -> FetchedPage: ...


class Parser(Protocol):
    def parse(self, page: FetchedPage) -> ParsedPayload: ...


@dataclass
class PipelineState:
    max_pages: int
    seen_urls: set[str] = field(default_factory=set)
    scheduled: int = 0
    completed: int = 0
    fetched: int = 0
    stored: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _inflight_fetch: int = 0
    _inflight_parse: int = 0

    async def can_schedule(self, url: str) -> bool:
        async with self.lock:
            return url not in self.seen_urls and self.scheduled < self.max_pages

    async def mark_scheduled(self, url: str) -> bool:
        async with self.lock:
            if url in self.seen_urls or self.scheduled >= self.max_pages:
                return False
            self.seen_urls.add(url)
            self.scheduled += 1
            return True

    async def mark_fetched(self) -> None:
        async with self.lock:
            self.fetched += 1

    async def mark_completed(self) -> None:
        async with self.lock:
            self.completed += 1

    async def add_stored(self, counts: dict[str, int]) -> None:
        async with self.lock:
            for table, count in counts.items():
                self.stored[table] += count

    async def enter_fetch(self) -> None:
        async with self.lock:
            self._inflight_fetch += 1

    async def exit_fetch(self) -> None:
        async with self.lock:
            self._inflight_fetch -= 1

    async def enter_parse(self) -> None:
        async with self.lock:
            self._inflight_parse += 1

    async def exit_parse(self) -> None:
        async with self.lock:
            self._inflight_parse -= 1

    async def is_idle(self, fetch_queue: asyncio.Queue[Any], parse_queue: asyncio.Queue[Any]) -> bool:
        async with self.lock:
            return (
                self._inflight_fetch == 0
                and self._inflight_parse == 0
                and self.completed >= self.scheduled
                and fetch_queue.empty()
                and parse_queue.empty()
            )


class ScrapePipeline:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    async def scrape_dotabuff(self, urls: Iterable[str] | None = None) -> dict[str, Any]:
        seeds = [
            FetchJob(url=url, source=Source.DOTABUFF, kind=PageKind.DOTABUFF_ESPORTS)
            for url in (urls or self.settings.dotabuff_seed_urls)
        ]
        parser = DotabuffParser(str(self.settings.dotabuff_base_url))
        async with DotabuffFetcher(
            fingerprint_seed=self.settings.browser_fingerprint_seed,
            delay_seconds=self.settings.dotabuff_delay_seconds,
        ) as fetcher:
            return await self._run(
                source=Source.DOTABUFF,
                seeds=seeds,
                fetcher=fetcher,
                parser=parser,
                fetcher_count=self.settings.dotabuff_concurrency,
            )

    async def scrape_liquipedia(self, urls: Iterable[str] | None = None) -> dict[str, Any]:
        seeds = [
            FetchJob(url=url, source=Source.LIQUIPEDIA, kind=PageKind.LIQUIPEDIA_PORTAL)
            for url in (urls or self.settings.liquipedia_seed_urls)
        ]
        parser = LiquipediaParser(str(self.settings.liquipedia_base_url))
        async with LiquipediaFetcher(
            user_agent=self.settings.user_agent,
            timeout_seconds=self.settings.request_timeout_seconds,
            delay_seconds=self.settings.liquipedia_delay_seconds,
        ) as fetcher:
            return await self._run(
                source=Source.LIQUIPEDIA,
                seeds=seeds,
                fetcher=fetcher,
                parser=parser,
                fetcher_count=self.settings.liquipedia_concurrency,
            )

    async def scrape_all(self, dotabuff_urls: Iterable[str] | None = None, liquipedia_urls: Iterable[str] | None = None) -> dict[str, Any]:
        dotabuff_result, liquipedia_result = await asyncio.gather(
            self.scrape_dotabuff(dotabuff_urls),
            self.scrape_liquipedia(liquipedia_urls),
        )
        return {"dotabuff": dotabuff_result, "liquipedia": liquipedia_result}

    async def scrape_opendota(
        self,
        match_id: int | None = None,
        since: date | None = None,
        until: date | None = None,
    ) -> dict[str, Any]:
        await self.database.init()
        known_team_names = await self.database.known_team_names()
        known_team_keys = {self._team_key(name) for name in known_team_names if name}

        since_ts = None
        if since is not None:
            since_ts = int(datetime.combine(since, time.min, tzinfo=timezone.utc).timestamp())

        until_ts = None
        if until is not None:
            until_ts = int(datetime.combine(until, time.max, tzinfo=timezone.utc).timestamp())

        processed_matches = 0
        skipped_matches = 0
        pages_fetched = 0
        stored_totals: dict[str, int] = defaultdict(int)

        async with OpenDotaFetcher(
            base_url=str(self.settings.opendota_base_url),
            user_agent=self.settings.user_agent,
            timeout_seconds=self.settings.request_timeout_seconds,
        ) as fetcher:
            hero_cache_path = self.settings.db_path.parent / "opendota_heroes_cache.json"
            hero_map = self._load_hero_cache(hero_cache_path)
            if not hero_map:
                heroes = await fetcher.get_heroes()
                hero_map = {
                    int(hero["id"]): str(hero.get("localized_name") or hero.get("name") or hero["id"])
                    for hero in heroes
                    if isinstance(hero, dict) and hero.get("id") is not None
                }
                self._save_hero_cache(hero_cache_path, hero_map)
            leagues = await fetcher.get_leagues()
            leagues_by_id = {
                int(league["leagueid"]): league
                for league in leagues
                if isinstance(league, dict) and league.get("leagueid") is not None
            }
            pro_players = await fetcher.get_pro_players()
            pro_players_by_id = {
                int(player["account_id"]): player
                for player in pro_players
                if isinstance(player, dict) and player.get("account_id") is not None
            }
            parser = OpenDotaParser(
                hero_names_by_id=hero_map,
                leagues_by_id=leagues_by_id,
                pro_players_by_account_id=pro_players_by_id,
            )

            team_cache: set[int] = set()
            matches: list[dict[str, Any]] = []
            if match_id is not None:
                matches = [{"match_id": match_id}]
            else:
                seen_match_ids: set[int] = set()
                less_than_match_id: int | None = None
                should_stop = False

                while pages_fetched < self.settings.max_pages_per_run and not should_stop:
                    page = await fetcher.get_pro_matches(less_than_match_id=less_than_match_id)
                    if not page:
                        break
                    pages_fetched += 1

                    page_ids: list[int] = []
                    for pro_match in page:
                        pro_match_id = self._int(pro_match.get("match_id"))
                        if pro_match_id is None:
                            continue
                        page_ids.append(pro_match_id)
                        if pro_match_id in seen_match_ids:
                            continue
                        seen_match_ids.add(pro_match_id)

                        start_time = self._int(pro_match.get("start_time"))
                        if since_ts is not None and start_time is not None and start_time < since_ts:
                            should_stop = True
                            continue

                        if until_ts is not None and start_time is not None and start_time > until_ts:
                            skipped_matches += 1
                            continue

                        if not self._match_involves_known_team(pro_match, known_team_keys):
                            skipped_matches += 1
                            continue
                        matches.append(pro_match)

                    if page_ids:
                        less_than_match_id = min(page_ids)
                    else:
                        break

            for pro_match in matches:
                current_match_id = self._int(pro_match.get("match_id"))
                if current_match_id is None:
                    continue

                if await self.database.opendota_match_exists(current_match_id):
                    skipped_matches += 1
                    continue

                radiant_id = self._team_id_from_match(pro_match, "radiant")
                dire_id = self._team_id_from_match(pro_match, "dire")
                for team_id in (radiant_id, dire_id):
                    if team_id is None or team_id in team_cache:
                        continue
                    team_payload = await fetcher.get_team(team_id)
                    team_rows = parser.parse_team_payload(team_payload)
                    team_counts = await self.database.insert_payload(team_rows)
                    for table, count in team_counts.items():
                        stored_totals[table] += count
                    team_cache.add(team_id)

                try:
                    details = await fetcher.get_match_details(current_match_id)
                except Exception as exc:
                    logger.warning("OpenDota match details failed for {}: {}", current_match_id, exc)
                    continue
                if not details:
                    continue

                parsed_rows = parser.parse_match_payload(pro_match=pro_match, match_details=details)
                counts = await self.database.upsert_opendota_payload(parsed_rows)
                for table, count in counts.items():
                    stored_totals[table] += count
                processed_matches += 1

        return {
            "source": Source.OPENDOTA.value,
            "requested_match_id": match_id,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "pages_fetched": pages_fetched,
            "candidate_matches": len(matches),
            "processed_matches": processed_matches,
            "skipped_matches": skipped_matches,
            "stored": dict(stored_totals),
        }

    async def scrape_dltv(
        self,
        live_only: bool = False,
        rankings_only: bool = False,
    ) -> dict[str, Any]:
        await self.database.init()
        parser = DltvParser(base_url=str(self.settings.dltv_base_url))
        fetched_at = now_utc_iso()
        stored_totals: dict[str, int] = defaultdict(int)
        jobs_run: list[str] = []

        run_live = live_only or not rankings_only
        run_rankings = rankings_only or not live_only
        run_transfers = not live_only and not rankings_only

        async with DltvFetcher(
            base_url=str(self.settings.dltv_base_url),
            user_agent=self.settings.user_agent,
            timeout_seconds=self.settings.request_timeout_seconds,
            delay_seconds=self.settings.dltv_delay_seconds,
            fingerprint_seed=self.settings.browser_fingerprint_seed,
        ) as fetcher:
            inspection = fetcher.api_inspection
            if inspection is None:
                inspection = await fetcher.inspect_internal_api()
            logger.info(
                "DLTV API inspection: search_api_usable={} status={} content_type={}",
                inspection.search_api_usable,
                inspection.search_api_status,
                inspection.search_api_content_type,
            )

            if run_live:
                config_payload = await fetcher.fetch_config(country_id=96)
                counts = await self.database.upsert_dltv_payload(parser.parse_config(config_payload))
                self._add_counts(stored_totals, counts)
                jobs_run.append("config")

                results_html = await fetcher.fetch_html_page("/results")
                counts = await self.database.upsert_dltv_payload(parser.parse_results_html(results_html))
                self._add_counts(stored_totals, counts)
                jobs_run.append("results")

            if run_rankings:
                world_html = await fetcher.fetch_html_page("/ranking")
                counts = await self.database.upsert_dltv_payload(
                    parser.parse_ranking_html(world_html, ranking_type="world", fetched_at=fetched_at)
                )
                self._add_counts(stored_totals, counts)
                jobs_run.append("ranking_world")

                ept_html = await fetcher.fetch_html_page("/ranking/ept")
                counts = await self.database.upsert_dltv_payload(
                    parser.parse_ranking_html(ept_html, ranking_type="ept", fetched_at=fetched_at)
                )
                self._add_counts(stored_totals, counts)
                jobs_run.append("ranking_ept")

            if run_transfers:
                transfers_html = await fetcher.fetch_html_page("/transfers")
                counts = await self.database.upsert_dltv_payload(parser.parse_transfers_html(transfers_html))
                self._add_counts(stored_totals, counts)
                jobs_run.append("transfers")

            await self.database.set_scraper_metadata("dltv_last_fetched", fetched_at)

        return {
            "source": Source.DLTV.value,
            "live_only": live_only,
            "rankings_only": rankings_only,
            "jobs_run": jobs_run,
            "fetched_at": fetched_at,
            "api_search_usable": inspection.search_api_usable if inspection else None,
            "stored": dict(stored_totals),
        }

    async def scrape_backfill(self, year: int | None, all_time: bool, source: str) -> dict[str, Any]:
        tasks = {}
        if source in ("all", "liquipedia"):
            lp_base = str(self.settings.liquipedia_base_url).rstrip("/")
            seeds = [
                FetchJob(url=f"{lp_base}/dota2/Tier_1_Tournaments", source=Source.LIQUIPEDIA, kind=PageKind.LIQUIPEDIA_PORTAL),
                FetchJob(url=f"{lp_base}/dota2/Tier_2_Tournaments", source=Source.LIQUIPEDIA, kind=PageKind.LIQUIPEDIA_PORTAL),
                FetchJob(url=f"{lp_base}/dota2/Tier_3_Tournaments", source=Source.LIQUIPEDIA, kind=PageKind.LIQUIPEDIA_PORTAL),
            ]
            parser = LiquipediaParser(lp_base)
            fetcher_cm = LiquipediaFetcher(
                user_agent=self.settings.user_agent,
                timeout_seconds=self.settings.request_timeout_seconds,
                delay_seconds=self.settings.liquipedia_delay_seconds,
            )
            tasks["liquipedia"] = self._run_with_cm(fetcher_cm, Source.LIQUIPEDIA, seeds, parser, self.settings.liquipedia_concurrency)

        if source in ("all", "dotabuff"):
            db_base = str(self.settings.dotabuff_base_url).rstrip("/")
            seeds = [FetchJob(url=f"{db_base}/esports/leagues", source=Source.DOTABUFF, kind=PageKind.DOTABUFF_ESPORTS)]
            parser = DotabuffParser(db_base)
            fetcher_cm = DotabuffFetcher(
                fingerprint_seed=self.settings.browser_fingerprint_seed,
                delay_seconds=self.settings.dotabuff_delay_seconds,
            )
            tasks["dotabuff"] = self._run_with_cm(fetcher_cm, Source.DOTABUFF, seeds, parser, self.settings.dotabuff_concurrency)
            
        if source in ("all", "opendota"):
            if all_time:
                since_date = None
                until_date = None
            else:
                assert year is not None
                since_date = date(year, 1, 1)
                until_date = date(year, 12, 31)
            tasks["opendota"] = self.scrape_opendota(since=since_date, until=until_date)

        names = list(tasks.keys())
        coros = list(tasks.values())
        gathered = await asyncio.gather(*coros)
        
        results = {}
        for name, result in zip(names, gathered):
            results[name] = result
            
        return results

    async def _run_with_cm(self, fetcher_cm: Any, source: Source, seeds: list[FetchJob], parser: Any, concurrency: int) -> dict[str, Any]:
        async with fetcher_cm as fetcher:
            return await self._run(source, seeds, fetcher, parser, concurrency)

    async def _run(
        self,
        source: Source,
        seeds: list[FetchJob],
        fetcher: Fetcher,
        parser: Parser,
        fetcher_count: int,
    ) -> dict[str, Any]:
        await self.database.init()
        fetch_queue: asyncio.Queue[FetchJob | None] = asyncio.Queue()
        parse_queue: asyncio.Queue[FetchedPage | None] = asyncio.Queue()
        state = PipelineState(max_pages=self.settings.max_pages_per_run)

        for job in seeds:
            if await state.mark_scheduled(job.url):
                await fetch_queue.put(job)

        progress = tqdm(total=self.settings.max_pages_per_run, desc=f"scrape {source.value}", unit="page")
        progress_lock = asyncio.Lock()

        async def fetch_worker(worker_id: int) -> None:
            while True:
                job = await fetch_queue.get()
                try:
                    if job is None:
                        return
                    await state.enter_fetch()
                    try:
                        page = await fetcher.fetch(job)
                        await state.mark_fetched()
                        await parse_queue.put(page)
                    except Exception as exc:
                        logger.warning("{} fetch failed for {}: {}", source.value, job.url, exc)
                        await state.mark_fetched()
                        await state.mark_completed()
                        async with progress_lock:
                            progress.update(1)
                    finally:
                        await state.exit_fetch()
                finally:
                    fetch_queue.task_done()

        async def parse_worker(worker_id: int) -> None:
            while True:
                page = await parse_queue.get()
                try:
                    if page is None:
                        return
                    await state.enter_parse()
                    try:
                        payload = parser.parse(page)
                        counts = await self.database.insert_payload(payload.rows)
                        await state.add_stored(counts)
                        for discovered in payload.discovered_jobs:
                            if discovered.source != source:
                                continue
                            if await state.mark_scheduled(discovered.url):
                                await fetch_queue.put(discovered)
                        await state.mark_completed()
                        async with progress_lock:
                            progress.update(1)
                    except Exception as exc:
                        logger.exception("{} parse/store failed: {}", source.value, exc)
                        await state.mark_completed()
                        async with progress_lock:
                            progress.update(1)
                    finally:
                        await state.exit_parse()
                finally:
                    parse_queue.task_done()

        workers = [asyncio.create_task(fetch_worker(i)) for i in range(fetcher_count)]
        workers += [asyncio.create_task(parse_worker(i)) for i in range(max(1, fetcher_count // 2))]

        try:
            while True:
                await asyncio.sleep(0.25)
                if await state.is_idle(fetch_queue, parse_queue):
                    break
            for _ in range(fetcher_count):
                await fetch_queue.put(None)
            for _ in range(max(1, fetcher_count // 2)):
                await parse_queue.put(None)
            await asyncio.gather(*workers)
        finally:
            progress.close()

        return {
            "source": source.value,
            "scheduled": state.scheduled,
            "fetched": state.fetched,
            "completed": state.completed,
            "stored": dict(state.stored),
        }

    @staticmethod
    def _add_counts(target: dict[str, int], counts: dict[str, int]) -> None:
        for table, count in counts.items():
            target[table] += int(count)

    @staticmethod
    def _int(value: Any) -> int | None:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _team_key(name: str | None) -> str:
        return re.sub(r"[^a-z0-9]+", "", (name or "").strip().lower())

    def _match_involves_known_team(self, match: dict[str, Any], known_team_keys: set[str]) -> bool:
        radiant_name = self._team_name_from_match(match, "radiant")
        dire_name = self._team_name_from_match(match, "dire")
        return bool(
            self._team_key(radiant_name) in known_team_keys
            or self._team_key(dire_name) in known_team_keys
        )

    @classmethod
    def _team_name_from_match(cls, match: dict[str, Any], side: str) -> str | None:
        payload = match.get(f"{side}_team")
        if isinstance(payload, dict):
            name = payload.get("name")
            return str(name).strip() if name else None
        alt_name = match.get(f"{side}_name")
        return str(alt_name).strip() if alt_name else None

    @classmethod
    def _team_id_from_match(cls, match: dict[str, Any], side: str) -> int | None:
        payload = match.get(f"{side}_team")
        if isinstance(payload, dict):
            return cls._int(payload.get("team_id") or payload.get("id"))
        if isinstance(payload, int):
            return payload
        return None

    @staticmethod
    def _load_hero_cache(path) -> dict[int, str]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return {int(key): str(value) for key, value in payload.items()}
        except Exception:
            return {}
        return {}

    @staticmethod
    def _save_hero_cache(path, hero_map: dict[int, str]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(hero_map, ensure_ascii=True, sort_keys=True), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to persist OpenDota hero cache at {}: {}", path, exc)
