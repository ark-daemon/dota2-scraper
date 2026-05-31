from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiosqlite
import pandas as pd
from loguru import logger

from dota2_scraper.utils import json_dumps

TABLES = (
    "teams",
    "players",
    "tournaments",
    "matches",
    "games",
    "drafts",
    "draft_picks",
    "player_game_stats",
    "game_timelines",
    "opendota_objectives",
    "ept_rankings",
    "world_rankings",
    "rosters",
    "staff",
    "standins",
    "transfers",
    "earnings",
    "scraper_metadata",
)


class Database:
    def __init__(self, db_path: Path, schema_path: Path) -> None:
        self.db_path = db_path
        self.schema_path = schema_path
        self._columns_cache: dict[str, set[str]] = {}
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def init(self) -> None:
        async with self._init_lock:
            if self._initialized:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            schema = self.schema_path.read_text(encoding="utf-8-sig")
            async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
                await db.execute("PRAGMA journal_mode = WAL")
                await db.execute("PRAGMA synchronous = NORMAL")
                await db.execute("PRAGMA foreign_keys = ON")
                await db.executescript(schema)
                await self._apply_migrations(db)
                await db.commit()
            logger.info("Database ready at {}", self.db_path)
            self._initialized = True

    async def table_counts(self) -> dict[str, int]:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            counts: dict[str, int] = {}
            for table in TABLES:
                async with db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                    row = await cursor.fetchone()
                    counts[table] = int(row[0]) if row else 0
            return counts

    async def _apply_migrations(self, db: aiosqlite.Connection) -> None:
        await self._add_column_if_missing(db, "games", "opendota_match_id", "INTEGER")
        await self._add_column_if_missing(db, "games", "radiant_gold_adv_json", "TEXT")
        await self._add_column_if_missing(db, "games", "radiant_xp_adv_json", "TEXT")
        await self._add_column_if_missing(db, "games", "duration_seconds", "INTEGER")
        await self._add_column_if_missing(db, "games", "patch", "INTEGER")

        await self._add_column_if_missing(db, "player_game_stats", "item_purchase_times_json", "TEXT")
        await self._add_column_if_missing(db, "player_game_stats", "obs_placed", "INTEGER")
        await self._add_column_if_missing(db, "player_game_stats", "sen_placed", "INTEGER")
        await self._add_column_if_missing(db, "player_game_stats", "teamfight_participation", "REAL")
        await self._add_column_if_missing(db, "player_game_stats", "gold_t_json", "TEXT")
        await self._add_column_if_missing(db, "player_game_stats", "xp_t_json", "TEXT")
        await self._add_column_if_missing(db, "player_game_stats", "lh_t_json", "TEXT")

        await self._add_column_if_missing(db, "drafts", "pick_ban_order", "INTEGER")
        await self._add_column_if_missing(db, "drafts", "hero_id", "INTEGER")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS opendota_objectives (
                game_id INTEGER REFERENCES games(id) ON DELETE CASCADE,
                time INTEGER,
                type TEXT,
                team INTEGER,
                key TEXT,
                slot INTEGER
            )
            """
        )
        await db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_opendota_objectives_unique
            ON opendota_objectives(game_id, time, type, team, key, slot)
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS ept_rankings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER REFERENCES teams(id),
                team_name TEXT,
                ept_points INTEGER,
                rank_position INTEGER,
                rank_delta INTEGER,
                fetched_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS world_rankings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER REFERENCES teams(id),
                team_name TEXT,
                ept_points INTEGER,
                rank_position INTEGER,
                rank_delta INTEGER,
                fetched_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER REFERENCES players(id),
                player_name TEXT NOT NULL,
                from_team TEXT,
                to_team TEXT,
                transfer_date TEXT,
                source TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS scraper_metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_transfers_unique
            ON transfers(player_name, from_team, to_team, transfer_date, source)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ept_rankings_fetched
            ON ept_rankings(fetched_at, rank_position)
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_world_rankings_fetched
            ON world_rankings(fetched_at, rank_position)
            """
        )

    async def _add_column_if_missing(
        self,
        db: aiosqlite.Connection,
        table: str,
        column: str,
        column_type: str,
    ) -> None:
        async with db.execute(f"PRAGMA table_info({table})") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        if column in columns:
            return
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
        self._columns_cache.pop(table, None)
        logger.info("Migration added {}.{}", table, column)

    async def known_team_names(self) -> set[str]:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            async with db.execute("SELECT name FROM teams WHERE name IS NOT NULL") as cursor:
                rows = await cursor.fetchall()
        return {str(row[0]).strip().lower() for row in rows if row and row[0]}

    async def set_scraper_metadata(self, key: str, value: str | None) -> None:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute(
                """
                INSERT INTO scraper_metadata(key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE
                SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """,
                (key, value),
            )
            await db.commit()

    async def get_scraper_metadata(self, key: str) -> str | None:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            async with db.execute("SELECT value FROM scraper_metadata WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
        return str(row[0]) if row and row[0] is not None else None

    async def opendota_match_exists(self, match_id: int) -> bool:
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            async with db.execute(
                "SELECT 1 FROM matches WHERE source = 'opendota' AND source_id = ? LIMIT 1",
                (str(match_id),)
            ) as cursor:
                return await cursor.fetchone() is not None


    async def upsert_dltv_payload(self, rows_by_table: Mapping[str, list[dict[str, Any]]]) -> dict[str, int]:
        inserted: dict[str, int] = {}
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute("PRAGMA foreign_keys = ON")

            team_rows = [dict(row) for row in rows_by_table.get("teams", [])]
            if team_rows:
                inserted["teams"] = await self._upsert_many(db, "teams", team_rows)

            tournament_rows = [dict(row) for row in rows_by_table.get("tournaments", [])]
            if tournament_rows:
                inserted["tournaments"] = await self._upsert_many(db, "tournaments", tournament_rows)

            team_name_map = await self._id_by_name(db, "teams", "name")
            tournament_name_map = await self._id_by_name(db, "tournaments", "name")
            player_name_map = await self._id_by_name(db, "players", "ign")

            match_rows = [dict(row) for row in rows_by_table.get("matches", [])]
            if match_rows:
                count = 0
                for row in match_rows:
                    match_id = await self._upsert_dltv_match(
                        db,
                        row,
                        team_name_map=team_name_map,
                        tournament_name_map=tournament_name_map,
                    )
                    if match_id:
                        count += 1
                inserted["matches"] = count

            for table in ("ept_rankings", "world_rankings"):
                ranking_rows = [dict(row) for row in rows_by_table.get(table, [])]
                if not ranking_rows:
                    continue
                values: list[tuple[Any, ...]] = []
                for row in ranking_rows:
                    team_name = row.get("team_name")
                    team_id = row.get("team_id")
                    if team_id is None and team_name:
                        team_id = self._resolve_name_id(team_name_map, None, team_name)
                    values.append(
                        (
                            team_id,
                            row.get("team_name"),
                            row.get("ept_points"),
                            row.get("rank_position"),
                            row.get("rank_delta"),
                            row.get("fetched_at"),
                        )
                    )
                await db.executemany(
                    f"""
                    INSERT INTO {table}(team_id, team_name, ept_points, rank_position, rank_delta, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                inserted[table] = len(values)

            transfer_rows = [dict(row) for row in rows_by_table.get("transfers", [])]
            if transfer_rows:
                count = 0
                for row in transfer_rows:
                    player_name = row.get("player_name")
                    player_id = row.get("player_id")
                    if player_id is None and player_name:
                        player_id = self._resolve_name_id(player_name_map, None, player_name)
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO transfers(
                            player_id, player_name, from_team, to_team, transfer_date, source
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            player_id,
                            player_name,
                            row.get("from_team"),
                            row.get("to_team"),
                            row.get("transfer_date"),
                            row.get("source") or "dltv",
                        ),
                    )
                    count += 1
                inserted["transfers"] = count

            await db.commit()
        return inserted

    async def upsert_opendota_payload(self, rows_by_table: Mapping[str, list[dict[str, Any]]]) -> dict[str, int]:
        inserted: dict[str, int] = {}
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute("PRAGMA foreign_keys = ON")

            team_rows = [dict(row) for row in rows_by_table.get("teams", [])]
            if team_rows:
                inserted["teams"] = await self._upsert_many(db, "teams", team_rows)

            player_rows = [dict(row) for row in rows_by_table.get("players", [])]
            if player_rows:
                inserted["players"] = await self._upsert_many(db, "players", player_rows)

            tournament_rows = [dict(row) for row in rows_by_table.get("tournaments", [])]
            if tournament_rows:
                inserted["tournaments"] = await self._upsert_many(db, "tournaments", tournament_rows)

            team_name_map = await self._id_by_name(db, "teams", "name")
            tournament_name_map = await self._id_by_name(db, "tournaments", "name")
            player_name_map = await self._id_by_name(db, "players", "ign")

            game_id_by_source: dict[str, int] = {}
            match_rows = [dict(row) for row in rows_by_table.get("matches", [])]
            game_rows = [dict(row) for row in rows_by_table.get("games", [])]

            if match_rows:
                count = 0
                for row in match_rows:
                    match_id = await self._upsert_match_by_source_id(
                        db,
                        row,
                        team_name_map=team_name_map,
                        tournament_name_map=tournament_name_map,
                    )
                    if match_id:
                        count += 1
                inserted["matches"] = count

            if game_rows:
                count = 0
                for row in game_rows:
                    game_id = await self._upsert_game_by_source_id(
                        db,
                        row,
                        team_name_map=team_name_map,
                    )
                    source_id = str(row.get("source_id") or "")
                    if game_id and source_id:
                        game_id_by_source[source_id] = game_id
                        count += 1
                inserted["games"] = count

            draft_rows = [dict(row) for row in rows_by_table.get("drafts", [])]
            if draft_rows:
                for row in draft_rows:
                    if row.get("game_id") is None:
                        source_id = str(row.get("_game_source_id") or "")
                        row["game_id"] = game_id_by_source.get(source_id)
                inserted["drafts"] = await self._upsert_many(db, "drafts", draft_rows)

            stat_rows = [dict(row) for row in rows_by_table.get("player_game_stats", [])]
            if stat_rows:
                count = 0
                for row in stat_rows:
                    source_id = str(row.get("_game_source_id") or "")
                    game_id = game_id_by_source.get(source_id)
                    if not game_id:
                        continue
                    row["game_id"] = game_id
                    if row.get("team_id") is None and row.get("team_name"):
                        row["team_id"] = self._resolve_name_id(team_name_map, row.get("source"), row.get("team_name"))
                    if row.get("player_id") is None and row.get("player_ign"):
                        row["player_id"] = self._resolve_name_id(player_name_map, row.get("source"), row.get("player_ign"))
                    updated = await self._merge_or_insert_player_game_stat(db, row)
                    if updated:
                        count += 1
                inserted["player_game_stats"] = count

            objectives = [dict(row) for row in rows_by_table.get("opendota_objectives", [])]
            if objectives:
                count = 0
                game_ids = {game_id_by_source.get(str(row.get("_game_source_id") or "")) for row in objectives}
                for game_id in {gid for gid in game_ids if gid is not None}:
                    await db.execute("DELETE FROM opendota_objectives WHERE game_id = ?", (game_id,))
                for row in objectives:
                    game_id = game_id_by_source.get(str(row.get("_game_source_id") or ""))
                    if not game_id:
                        continue
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO opendota_objectives(game_id, time, type, team, key, slot)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            game_id,
                            row.get("time"),
                            row.get("type"),
                            row.get("team"),
                            row.get("key"),
                            row.get("slot"),
                        ),
                    )
                    count += 1
                inserted["opendota_objectives"] = count

            await db.commit()
        return inserted

    async def _upsert_dltv_match(
        self,
        db: aiosqlite.Connection,
        row: dict[str, Any],
        team_name_map: dict[tuple[str | None, str | None], int],
        tournament_name_map: dict[tuple[str | None, str | None], int],
    ) -> int | None:
        source = row.get("source") or "dltv"
        source_id = str(row.get("source_id") or "")
        team_a_name = row.get("team_a_name")
        team_b_name = row.get("team_b_name")
        tournament_name = row.get("tournament_name")
        scheduled_at_utc = row.get("scheduled_at_utc")
        completed_at_utc = row.get("completed_at_utc")
        date_key = (completed_at_utc or scheduled_at_utc or "")[:10]

        team_a_id = row.get("team_a_id") or self._resolve_name_id(team_name_map, source, team_a_name)
        team_b_id = row.get("team_b_id") or self._resolve_name_id(team_name_map, source, team_b_name)
        tournament_id = row.get("tournament_id") or self._resolve_name_id(
            tournament_name_map, source, tournament_name
        )

        existing_id: int | None = None
        if source_id:
            async with db.execute(
                """
                SELECT id FROM matches
                WHERE source_id = ?
                ORDER BY CASE source WHEN 'dotabuff' THEN 0 WHEN 'opendota' THEN 1 WHEN 'dltv' THEN 2 ELSE 3 END
                LIMIT 1
                """,
                (source_id,),
            ) as cursor:
                existing = await cursor.fetchone()
            existing_id = int(existing[0]) if existing else None

        if existing_id is None and team_a_name and team_b_name and tournament_name and date_key:
            async with db.execute(
                """
                SELECT id FROM matches
                WHERE (
                        (LOWER(COALESCE(team_a_name, '')) = LOWER(?) AND LOWER(COALESCE(team_b_name, '')) = LOWER(?))
                     OR (LOWER(COALESCE(team_a_name, '')) = LOWER(?) AND LOWER(COALESCE(team_b_name, '')) = LOWER(?))
                )
                  AND LOWER(COALESCE(tournament_name, '')) = LOWER(?)
                  AND substr(COALESCE(completed_at_utc, scheduled_at_utc, ''), 1, 10) = ?
                ORDER BY CASE source WHEN 'dotabuff' THEN 0 WHEN 'opendota' THEN 1 WHEN 'dltv' THEN 2 ELSE 3 END
                LIMIT 1
                """,
                (team_a_name, team_b_name, team_b_name, team_a_name, tournament_name, date_key),
            ) as cursor:
                existing = await cursor.fetchone()
            existing_id = int(existing[0]) if existing else None

        if existing_id is not None:
            await db.execute(
                """
                UPDATE matches
                SET tournament_id = COALESCE(?, tournament_id),
                    tournament_name = COALESCE(?, tournament_name),
                    team_a_id = COALESCE(?, team_a_id),
                    team_b_id = COALESCE(?, team_b_id),
                    team_a_name = COALESCE(?, team_a_name),
                    team_b_name = COALESCE(?, team_b_name),
                    team_a_score = COALESCE(?, team_a_score),
                    team_b_score = COALESCE(?, team_b_score),
                    series_format = COALESCE(?, series_format),
                    scheduled_at_utc = COALESCE(?, scheduled_at_utc),
                    completed_at_utc = COALESCE(?, completed_at_utc),
                    status = COALESCE(?, status),
                    raw_json = COALESCE(?, raw_json),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    tournament_id,
                    tournament_name,
                    team_a_id,
                    team_b_id,
                    team_a_name,
                    team_b_name,
                    row.get("team_a_score"),
                    row.get("team_b_score"),
                    row.get("series_format"),
                    scheduled_at_utc,
                    completed_at_utc,
                    row.get("status"),
                    row.get("raw_json"),
                    existing_id,
                ),
            )
            return existing_id

        await db.execute(
            """
            INSERT INTO matches(
                source, source_id, tournament_id, tournament_name, team_a_id, team_b_id,
                team_a_name, team_b_name, team_a_score, team_b_score, series_format,
                scheduled_at_utc, completed_at_utc, status, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                source_id or None,
                tournament_id,
                tournament_name,
                team_a_id,
                team_b_id,
                team_a_name,
                team_b_name,
                row.get("team_a_score"),
                row.get("team_b_score"),
                row.get("series_format"),
                scheduled_at_utc,
                completed_at_utc,
                row.get("status"),
                row.get("raw_json"),
            ),
        )
        async with db.execute(
            """
            SELECT id FROM matches
            WHERE source = ? AND source_id IS ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (source, source_id or None),
        ) as cursor:
            inserted = await cursor.fetchone()
        return int(inserted[0]) if inserted else None

    async def _upsert_match_by_source_id(
        self,
        db: aiosqlite.Connection,
        row: dict[str, Any],
        team_name_map: dict[tuple[str | None, str | None], int],
        tournament_name_map: dict[tuple[str | None, str | None], int],
    ) -> int | None:
        source_id = str(row.get("source_id") or "")
        if not source_id:
            return None
        source = row.get("source")
        team_a_id = row.get("team_a_id") or self._resolve_name_id(team_name_map, source, row.get("team_a_name"))
        team_b_id = row.get("team_b_id") or self._resolve_name_id(team_name_map, source, row.get("team_b_name"))
        tournament_id = row.get("tournament_id") or self._resolve_name_id(
            tournament_name_map, source, row.get("tournament_name")
        )

        async with db.execute(
            """
            SELECT id FROM matches
            WHERE source_id = ?
            ORDER BY CASE source WHEN 'dotabuff' THEN 0 WHEN 'opendota' THEN 1 ELSE 2 END
            LIMIT 1
            """,
            (source_id,),
        ) as cursor:
            existing = await cursor.fetchone()

        if existing:
            match_id = int(existing[0])
            await db.execute(
                """
                UPDATE matches
                SET tournament_id = COALESCE(?, tournament_id),
                    tournament_name = COALESCE(?, tournament_name),
                    region = COALESCE(?, region),
                    team_a_id = COALESCE(?, team_a_id),
                    team_b_id = COALESCE(?, team_b_id),
                    team_a_name = COALESCE(?, team_a_name),
                    team_b_name = COALESCE(?, team_b_name),
                    team_a_score = COALESCE(?, team_a_score),
                    team_b_score = COALESCE(?, team_b_score),
                    series_format = COALESCE(?, series_format),
                    patch_version = COALESCE(?, patch_version),
                    scheduled_at_utc = COALESCE(?, scheduled_at_utc),
                    completed_at_utc = COALESCE(?, completed_at_utc),
                    status = COALESCE(?, status),
                    raw_json = COALESCE(?, raw_json),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    tournament_id,
                    row.get("tournament_name"),
                    row.get("region"),
                    team_a_id,
                    team_b_id,
                    row.get("team_a_name"),
                    row.get("team_b_name"),
                    row.get("team_a_score"),
                    row.get("team_b_score"),
                    row.get("series_format"),
                    row.get("patch_version"),
                    row.get("scheduled_at_utc"),
                    row.get("completed_at_utc"),
                    row.get("status"),
                    row.get("raw_json"),
                    match_id,
                ),
            )
            return match_id

        await db.execute(
            """
            INSERT INTO matches(
                source, source_id, tournament_id, tournament_name, team_a_id, team_b_id,
                team_a_name, team_b_name, team_a_score, team_b_score, series_format, patch_version,
                region, scheduled_at_utc, completed_at_utc, status, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("source"),
                source_id,
                tournament_id,
                row.get("tournament_name"),
                team_a_id,
                team_b_id,
                row.get("team_a_name"),
                row.get("team_b_name"),
                row.get("team_a_score"),
                row.get("team_b_score"),
                row.get("series_format"),
                row.get("patch_version"),
                row.get("region"),
                row.get("scheduled_at_utc"),
                row.get("completed_at_utc"),
                row.get("status"),
                row.get("raw_json"),
            ),
        )
        async with db.execute(
            """
            SELECT id FROM matches
            WHERE source = ? AND source_id = ?
            LIMIT 1
            """,
            (row.get("source"), source_id),
        ) as cursor:
            inserted = await cursor.fetchone()
        return int(inserted[0]) if inserted else None

    async def _upsert_game_by_source_id(
        self,
        db: aiosqlite.Connection,
        row: dict[str, Any],
        team_name_map: dict[tuple[str | None, str | None], int],
    ) -> int | None:
        source_id = str(row.get("source_id") or "")
        if not source_id:
            return None

        async with db.execute(
            """
            SELECT id, match_id FROM games
            WHERE source_id = ?
            ORDER BY CASE source WHEN 'dotabuff' THEN 0 WHEN 'opendota' THEN 1 ELSE 2 END
            LIMIT 1
            """,
            (source_id,),
        ) as cursor:
            existing = await cursor.fetchone()

        match_fk = row.get("match_id")
        if match_fk is None:
            async with db.execute(
                """
                SELECT id FROM matches
                WHERE source_id = ?
                ORDER BY CASE source WHEN 'dotabuff' THEN 0 WHEN 'opendota' THEN 1 ELSE 2 END
                LIMIT 1
                """,
                (source_id,),
            ) as cursor:
                match_row = await cursor.fetchone()
            match_fk = int(match_row[0]) if match_row else None

        source = row.get("source")
        radiant_team_id = row.get("radiant_team_id") or self._resolve_name_id(
            team_name_map, source, row.get("radiant_team_name")
        )
        dire_team_id = row.get("dire_team_id") or self._resolve_name_id(
            team_name_map, source, row.get("dire_team_name")
        )

        if existing:
            game_id = int(existing[0])
            await db.execute(
                """
                UPDATE games
                SET match_id = COALESCE(?, match_id),
                    radiant_team_id = COALESCE(?, radiant_team_id),
                    dire_team_id = COALESCE(?, dire_team_id),
                    radiant_team_name = COALESCE(?, radiant_team_name),
                    dire_team_name = COALESCE(?, dire_team_name),
                    winning_side = COALESCE(?, winning_side),
                    duration_seconds = COALESCE(?, duration_seconds),
                    opendota_match_id = COALESCE(?, opendota_match_id),
                    radiant_gold_adv_json = COALESCE(?, radiant_gold_adv_json),
                    radiant_xp_adv_json = COALESCE(?, radiant_xp_adv_json),
                    patch = COALESCE(?, patch),
                    raw_json = COALESCE(?, raw_json),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    match_fk,
                    radiant_team_id,
                    dire_team_id,
                    row.get("radiant_team_name"),
                    row.get("dire_team_name"),
                    row.get("winning_side"),
                    row.get("duration_seconds"),
                    row.get("opendota_match_id"),
                    row.get("radiant_gold_adv_json"),
                    row.get("radiant_xp_adv_json"),
                    row.get("patch"),
                    row.get("raw_json"),
                    game_id,
                ),
            )
            return game_id

        await db.execute(
            """
            INSERT INTO games(
                match_id, source, source_id, game_number,
                radiant_team_id, dire_team_id, radiant_team_name, dire_team_name,
                winning_side, duration_seconds, opendota_match_id,
                radiant_gold_adv_json, radiant_xp_adv_json, patch, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_fk,
                row.get("source"),
                source_id,
                row.get("game_number"),
                radiant_team_id,
                dire_team_id,
                row.get("radiant_team_name"),
                row.get("dire_team_name"),
                row.get("winning_side"),
                row.get("duration_seconds"),
                row.get("opendota_match_id"),
                row.get("radiant_gold_adv_json"),
                row.get("radiant_xp_adv_json"),
                row.get("patch"),
                row.get("raw_json"),
            ),
        )
        async with db.execute(
            """
            SELECT id FROM games
            WHERE source = ? AND source_id = ?
            LIMIT 1
            """,
            (row.get("source"), source_id),
        ) as cursor:
            inserted = await cursor.fetchone()
        return int(inserted[0]) if inserted else None

    async def _merge_or_insert_player_game_stat(self, db: aiosqlite.Connection, row: dict[str, Any]) -> bool:
        game_id = row.get("game_id")
        hero_name = row.get("hero_name")
        source_id = row.get("source_id")
        if not game_id or not source_id:
            return False

        existing_id = None
        if hero_name:
            async with db.execute(
                """
                SELECT id FROM player_game_stats
                WHERE game_id = ? AND LOWER(hero_name) = LOWER(?)
                ORDER BY CASE source WHEN 'dotabuff' THEN 0 WHEN 'opendota' THEN 1 ELSE 2 END
                LIMIT 1
                """,
                (game_id, hero_name),
            ) as cursor:
                existing = await cursor.fetchone()
            existing_id = int(existing[0]) if existing else None

        if existing_id:
            await db.execute(
                """
                UPDATE player_game_stats
                SET player_id = COALESCE(?, player_id),
                    team_id = COALESCE(?, team_id),
                    player_ign = COALESCE(?, player_ign),
                    team_name = COALESCE(?, team_name),
                    kills = COALESCE(?, kills),
                    deaths = COALESCE(?, deaths),
                    assists = COALESCE(?, assists),
                    gpm = COALESCE(?, gpm),
                    xpm = COALESCE(?, xpm),
                    last_hits = COALESCE(?, last_hits),
                    denies = COALESCE(?, denies),
                    hero_damage = COALESCE(?, hero_damage),
                    tower_damage = COALESCE(?, tower_damage),
                    hero_healing = COALESCE(?, hero_healing),
                    final_items_json = COALESCE(?, final_items_json),
                    backpack_items_json = COALESCE(?, backpack_items_json),
                    neutral_item = COALESCE(?, neutral_item),
                    item_purchase_times_json = COALESCE(?, item_purchase_times_json),
                    obs_placed = COALESCE(?, obs_placed),
                    sen_placed = COALESCE(?, sen_placed),
                    observer_wards_placed = COALESCE(?, observer_wards_placed),
                    sentry_wards_placed = COALESCE(?, sentry_wards_placed),
                    camps_stacked = COALESCE(?, camps_stacked),
                    teamfight_participation = COALESCE(?, teamfight_participation),
                    teamfight_participation_pct = COALESCE(?, teamfight_participation_pct),
                    gold_t_json = COALESCE(?, gold_t_json),
                    xp_t_json = COALESCE(?, xp_t_json),
                    lh_t_json = COALESCE(?, lh_t_json),
                    raw_json = COALESCE(?, raw_json),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    row.get("player_id"),
                    row.get("team_id"),
                    row.get("player_ign"),
                    row.get("team_name"),
                    row.get("kills"),
                    row.get("deaths"),
                    row.get("assists"),
                    row.get("gpm"),
                    row.get("xpm"),
                    row.get("last_hits"),
                    row.get("denies"),
                    row.get("hero_damage"),
                    row.get("tower_damage"),
                    row.get("hero_healing"),
                    row.get("final_items_json"),
                    row.get("backpack_items_json"),
                    row.get("neutral_item"),
                    row.get("item_purchase_times_json"),
                    row.get("obs_placed"),
                    row.get("sen_placed"),
                    row.get("observer_wards_placed"),
                    row.get("sentry_wards_placed"),
                    row.get("camps_stacked"),
                    row.get("teamfight_participation"),
                    row.get("teamfight_participation_pct"),
                    row.get("gold_t_json"),
                    row.get("xp_t_json"),
                    row.get("lh_t_json"),
                    row.get("raw_json"),
                    existing_id,
                ),
            )
            return True

        await self._upsert_many(db, "player_game_stats", [row])
        return True

    @staticmethod
    def _resolve_name_id(
        mapping: dict[tuple[str | None, str | None], int],
        source: str | None,
        name: Any,
    ) -> int | None:
        if not isinstance(name, str) or not name:
            return None
        direct = mapping.get((source, name))
        if direct is not None:
            return direct
        lowered = name.lower()
        for (mapped_source, mapped_name), mapped_id in mapping.items():
            if mapped_name and mapped_name.lower() == lowered:
                return mapped_id
        return None

    async def insert_payload(self, rows_by_table: Mapping[str, list[dict[str, Any]]]) -> dict[str, int]:
        inserted: dict[str, int] = {}
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            ordered_tables = [table for table in TABLES if rows_by_table.get(table)]
            for table in ordered_tables:
                rows = [dict(row) for row in rows_by_table.get(table, [])]
                if not rows:
                    continue
                await self._enrich_foreign_keys(db, table, rows)
                count = await self._upsert_many(db, table, rows)
                inserted[table] = count
            await db.commit()
        return inserted

    async def _enrich_foreign_keys(
        self,
        db: aiosqlite.Connection,
        table: str,
        rows: list[dict[str, Any]],
    ) -> None:
        source_id_maps = {
            "teams": await self._id_by_source_id(db, "teams"),
            "players": await self._id_by_source_id(db, "players"),
            "tournaments": await self._id_by_source_id(db, "tournaments"),
            "matches": await self._id_by_source_id(db, "matches"),
            "games": await self._id_by_source_id(db, "games"),
            "drafts": await self._id_by_source_id(db, "drafts"),
        }
        name_maps = {
            "teams": await self._id_by_name(db, "teams", "name"),
            "players": await self._id_by_name(db, "players", "ign"),
            "tournaments": await self._id_by_name(db, "tournaments", "name"),
        }
        cross_source_maps = {
            "teams": {name.lower(): id for (_, name), id in name_maps["teams"].items() if name},
            "players": {name.lower(): id for (_, name), id in name_maps["players"].items() if name},
            "tournaments": {name.lower(): id for (_, name), id in name_maps["tournaments"].items() if name},
        }

        def _resolve_id(table_name: str, src: str | None, name: str | None) -> int | None:
            if not name:
                return None
            exact = name_maps[table_name].get((src, name))
            if exact is not None:
                return exact
            return cross_source_maps[table_name].get(name.lower())

        def fill(row: dict, key: str, value: Any) -> None:
            if row.get(key) is None and value is not None:
                row[key] = value

        for row in rows:
            source = row.get("source")

            if table == "matches":
                fill(row, "team_a_id", _resolve_id("teams", source, row.get("team_a_name")))
                fill(row, "team_b_id", _resolve_id("teams", source, row.get("team_b_name")))
                fill(row, "tournament_id", _resolve_id("tournaments", source, row.get("tournament_name")))
            elif table == "games":
                game_source_id = row.get("_match_source_id") or row.get("source_id")
                fill(row, "match_id", source_id_maps["matches"].get((source, game_source_id)))
                fill(row, "radiant_team_id", _resolve_id("teams", source, row.get("radiant_team_name")))
                fill(row, "dire_team_id", _resolve_id("teams", source, row.get("dire_team_name")))
            elif table == "drafts":
                fill(row, "game_id", source_id_maps["games"].get((source, row.get("_game_source_id") or row.get("source_id"))))
                fill(row, "first_pick_team_id", _resolve_id("teams", source, row.get("first_pick_team_name")))
            elif table == "draft_picks":
                fill(row, "game_id", source_id_maps["games"].get((source, row.get("_game_source_id"))))
                fill(row, "draft_id", source_id_maps["drafts"].get((source, row.get("_draft_source_id"))))
                fill(row, "team_id", _resolve_id("teams", source, row.get("team_name")))
            elif table == "player_game_stats":
                fill(row, "game_id", source_id_maps["games"].get((source, row.get("_game_source_id"))))
                fill(row, "team_id", _resolve_id("teams", source, row.get("team_name")))
                fill(row, "player_id", _resolve_id("players", source, row.get("player_ign")))
            elif table == "game_timelines":
                fill(row, "game_id", source_id_maps["games"].get((source, row.get("_game_source_id"))))
            elif table in {"rosters", "standins", "staff"}:
                fill(row, "team_id", _resolve_id("teams", source, row.get("team_name")))
                if table in {"rosters", "standins"}:
                    fill(row, "player_id", _resolve_id("players", source, row.get("player_ign")))
                if table == "standins":
                    fill("tournament_id", _resolve_id("tournaments", source, row.get("tournament_name")))
            elif table == "earnings":
                fill("team_id", _resolve_id("teams", source, row.get("team_name")))
                fill("player_id", _resolve_id("players", source, row.get("player_ign")))
                fill("tournament_id", _resolve_id("tournaments", source, row.get("tournament_name")))

    @staticmethod
    async def _id_by_source_id(
        db: aiosqlite.Connection,
        table: str,
    ) -> dict[tuple[str | None, str | None], int]:
        async with db.execute(f"SELECT id, source, source_id FROM {table}") as cursor:
            rows = await cursor.fetchall()
        return {(row[1], row[2]): row[0] for row in rows}

    @staticmethod
    async def _id_by_name(
        db: aiosqlite.Connection,
        table: str,
        name_column: str,
    ) -> dict[tuple[str | None, str | None], int]:
        async with db.execute(f"SELECT id, source, {name_column} FROM {table}") as cursor:
            rows = await cursor.fetchall()
        return {(row[1], row[2]): row[0] for row in rows if row[2]}

    async def _columns(self, db: aiosqlite.Connection, table: str) -> set[str]:
        if table in self._columns_cache:
            return self._columns_cache[table]
        async with db.execute(f"PRAGMA table_info({table})") as cursor:
            rows = await cursor.fetchall()
        columns = {row[1] for row in rows}
        self._columns_cache[table] = columns
        return columns

    async def _upsert_many(
        self,
        db: aiosqlite.Connection,
        table: str,
        rows: list[dict[str, Any]],
    ) -> int:
        columns = await self._columns(db, table)
        prepared = [self._prepare_row(row, columns) for row in rows]
        prepared = [row for row in prepared if row]
        if not prepared:
            return 0

        all_columns = sorted({column for row in prepared for column in row})
        placeholders = ", ".join("?" for _ in all_columns)
        column_sql = ", ".join(all_columns)
        update_columns = [
            column
            for column in all_columns
            if column not in {"id", "created_at", "source", "source_id"}
        ]
        update_sql = ", ".join(
            [f"{column}=excluded.{column}" for column in update_columns]
            + ["updated_at=CURRENT_TIMESTAMP"]
        )
        conflict_sql = ""
        if {"source", "source_id"}.issubset(all_columns):
            conflict_sql = f" ON CONFLICT(source, source_id) DO UPDATE SET {update_sql}"

        sql = f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}){conflict_sql}"
        values = [tuple(row.get(column) for column in all_columns) for row in prepared]
        await db.executemany(sql, values)
        logger.debug("Stored {} row(s) in {}", len(values), table)
        return len(values)

    @staticmethod
    def _prepare_row(row: dict[str, Any], columns: set[str]) -> dict[str, Any]:
        prepared: dict[str, Any] = {}
        for key, value in row.items():
            if key not in columns or value is None:
                continue
            if isinstance(value, (dict, list, tuple)):
                prepared[key] = json_dumps(value)
            else:
                prepared[key] = value
        return prepared


def export_tables(db_path: Path, export_dir: Path) -> dict[str, Path]:
    export_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    with sqlite3.connect(db_path) as conn:
        for table in TABLES:
            frame = pd.read_sql_query(f"SELECT * FROM {table}", conn)
            path = export_dir / f"{table}.parquet"
            frame.to_parquet(path, index=False)
            written[table] = path
    return written
