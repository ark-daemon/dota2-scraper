from __future__ import annotations

import re
from typing import Any

from loguru import logger
from selectolax.parser import HTMLParser, Node

from dota2_scraper.models import FetchJob, FetchedPage, PageKind, ParsedPayload, Source
from dota2_scraper.utils import (
    absolute_url,
    clean_text,
    detect_region,
    json_dumps,
    parse_duration_seconds,
    parse_float,
    parse_int,
    parse_pct,
    side_from_text,
    stable_id,
)

MATCH_RE = re.compile(r"/matches/(\d+)")
TEAM_RE = re.compile(r"/esports/teams/(\d+)")
HERO_RE = re.compile(r"/heroes/([a-z0-9-]+)")
SCORE_RE = re.compile(r"\b\d+\s*-\s*\d+\b")
BEST_OF_RE = re.compile(r"\bBo\s?([1253])\b|Best of\s+(\d)", re.I)
SERIES_SCORE_RE = re.compile(r"\b(\d+)\s*[-:]\s*(\d+)\b")
PATCH_RE = re.compile(r"\b7\.\d{2}[a-z]?\b", re.I)
PICKBAN_HERO_RE = re.compile(r"(?:Pick|Ban)\s+([A-Z][A-Za-z' -]{2,})")
PICKBAN_ALT_RE = re.compile(r"(.+?)\s+(?:picked|banned|pick|ban)\b", re.I)
RADIANT_VICTORY_RE = re.compile(r"Radiant\s+Victory", re.I)
DIRE_VICTORY_RE = re.compile(r"Dire\s+Victory", re.I)
GAME_NUMBER_RE = re.compile(r"Game\s*(\d+)", re.I)
TIME_RE = re.compile(r"^\d{1,2}:\d{2}")
MEGA_CREEPS_RE = re.compile(r"(Radiant|Dire).{0,30}mega creeps", re.I)
FIRST_BLOOD_RE = re.compile(r"first blood.{0,80}?(\d{1,2}:\d{2})", re.I)


class DotabuffParser:
    """Defensive Dotabuff parser for esports listings, teams, and match/game pages."""

    def __init__(self, base_url: str = "https://www.dotabuff.com") -> None:
        self.base_url = base_url.rstrip("/")

    def parse(self, page: FetchedPage) -> ParsedPayload:
        tree = HTMLParser(page.html)
        payload = ParsedPayload(source=Source.DOTABUFF, url=page.final_url)
        self._discover_links(tree, payload, page.job.depth)

        try:
            if "/matches/" in page.final_url:
                self._parse_match_page(tree, page, payload)
            elif "/esports/teams/" in page.final_url:
                self._parse_team_page(tree, page, payload)
            else:
                self._parse_esports_listing(tree, page, payload)
        except Exception as exc:  # defensive parser: log and keep discovered URLs
            logger.exception("Dotabuff parser recovered from {} on {}", exc, page.final_url)
        return payload

    def _discover_links(self, tree: HTMLParser, payload: ParsedPayload, depth: int) -> None:
        if depth >= 3:
            return
        seen: set[str] = set()
        
        base_current = payload.url.split('?')[0]
        
        for link in tree.css("a[href]"):
            href = link.attributes.get("href")
            url = absolute_url(self.base_url, href)
            if not url or url in seen:
                continue
            seen.add(url)
            
            kind = PageKind.UNKNOWN
            if MATCH_RE.search(url):
                kind = PageKind.DOTABUFF_MATCH
            elif TEAM_RE.search(url):
                kind = PageKind.DOTABUFF_TEAM
            elif "/esports/leagues" in url:
                kind = PageKind.DOTABUFF_ESPORTS
            else:
                continue
                
            base_next = url.split('?')[0]
            if base_current == base_next and "page=" in url:
                next_depth = depth
            else:
                next_depth = depth + 1
                
            if next_depth <= 3:
                payload.discovered_jobs.append(
                    FetchJob(url=url, source=Source.DOTABUFF, kind=kind, depth=next_depth)
                )

    def _parse_esports_listing(
        self, tree: HTMLParser, page: FetchedPage, payload: ParsedPayload
    ) -> None:
        for row in tree.css("table tr"):
            text = clean_text(row.text()) or ""
            match_link = self._first_href(row, MATCH_RE)
            if not match_link:
                continue
            source_id = self._match_id(match_link) or stable_id(page.final_url, text)
            teams = self._team_names_from_node(row)
            payload.add(
                "matches",
                {
                    "source": Source.DOTABUFF.value,
                    "source_id": source_id,
                    "team_a_name": teams[0] if len(teams) > 0 else None,
                    "team_b_name": teams[1] if len(teams) > 1 else None,
                    "tournament_name": self._nearest_text(row, ["a[href*='/esports/leagues/']", ".event", ".league", "[class*=event]"]),
                    "region": detect_region(text),
                    "series_format": self._series_format(text),
                    "status": "completed" if SCORE_RE.search(text) else None,
                    "raw_json": json_dumps({"text": text, "url": match_link}),
                },
            )

        for team_link in tree.css('a[href*="/esports/teams/"]'):
            name = clean_text(team_link.text())
            href = team_link.attributes.get("href")
            team_id = self._team_id(href)
            if name and team_id:
                payload.add(
                    "teams",
                    {
                        "source": Source.DOTABUFF.value,
                        "source_id": team_id,
                        "name": name,
                        "raw_json": json_dumps({"url": absolute_url(self.base_url, href)}),
                    },
                )

    def _parse_team_page(self, tree: HTMLParser, page: FetchedPage, payload: ParsedPayload) -> None:
        team_id = self._team_id(page.final_url) or stable_id(page.final_url)
        title = self._page_title(tree)
        page_text = clean_text(tree.body.text() if tree.body else tree.text()) or ""
        stats = self._key_value_text(tree)
        payload.add(
            "teams",
            {
                "source": Source.DOTABUFF.value,
                "source_id": team_id,
                "name": title or team_id,
                "region": detect_region(page_text),
                "record": stats.get("record"),
                "rating": parse_float(stats.get("rating")),
                "radiant_win_rate": parse_pct(stats.get("radiant win rate")),
                "dire_win_rate": parse_pct(stats.get("dire win rate")),
                "avg_game_duration_seconds": parse_duration_seconds(stats.get("average duration")),
                "avg_net_worth_diff_10": parse_float(stats.get("net worth 10")),
                "avg_net_worth_diff_15": parse_float(stats.get("net worth 15")),
                "avg_net_worth_diff_20": parse_float(stats.get("net worth 20")),
                "raw_json": json_dumps({"stats": stats}),
            },
        )

    def _parse_match_page(self, tree: HTMLParser, page: FetchedPage, payload: ParsedPayload) -> None:
        source_id = self._match_id(page.final_url) or stable_id(page.final_url)
        page_text = clean_text(tree.body.text() if tree.body else tree.text()) or ""
        teams = self._team_names_from_node(tree.root)
        radiant, dire = self._side_assignments(tree, teams)
        score = self._score(page_text)
        title = self._page_title(tree)
        tournament = self._nearest_text(tree.root, ["a[href*='/esports/leagues/']", ".event", ".league", "[class*=event]"])
        patch = self._patch(page_text)
        duration = parse_duration_seconds(self._nearest_text(tree.root, [".duration", "[class*=duration]"]))

        payload.add(
            "matches",
            {
                "source": Source.DOTABUFF.value,
                "source_id": source_id,
                "tournament_name": tournament,
                "region": detect_region(page_text),
                "team_a_name": teams[0] if len(teams) > 0 else None,
                "team_b_name": teams[1] if len(teams) > 1 else None,
                "team_a_score": score[0],
                "team_b_score": score[1],
                "series_format": self._series_format(page_text),
                "patch_version": patch,
                "status": "completed",
                "raw_json": json_dumps({"title": title, "url": page.final_url}),
            },
        )
        payload.add(
            "games",
            {
                "source": Source.DOTABUFF.value,
                "source_id": source_id,
                "game_number": self._game_number(tree, page_text),
                "radiant_team_name": radiant,
                "dire_team_name": dire,
                "winning_side": self._winning_side(tree, page_text),
                "duration_seconds": duration,
                "patch_version": patch,
                "mega_creeps_team": self._mega_creeps(page_text),
                "raw_json": json_dumps({"match_source_id": source_id}),
            },
        )
        draft_id = stable_id("dotabuff-draft", source_id)
        payload.add(
            "drafts",
            {
                "source": Source.DOTABUFF.value,
                "source_id": draft_id,
                "first_pick_team_name": self._first_pick_team(tree),
                "_game_source_id": source_id,
                "raw_json": json_dumps({"game_source_id": source_id}),
            },
        )
        for pick in self._parse_draft_picks(tree, source_id, draft_id, radiant, dire):
            payload.add("draft_picks", pick)
        for stat in self._parse_player_stats(tree, source_id):
            payload.add("player_game_stats", stat)
        payload.add("game_timelines", self._parse_timeline(tree, source_id, page_text))

    def _parse_draft_picks(
        self,
        tree: HTMLParser,
        match_source_id: str,
        draft_source_id: str,
        radiant: str | None,
        dire: str | None,
    ) -> list[dict[str, Any]]:
        # Dotabuff renders two blocks: one .picks-inline per side.
        # The first block is Radiant's picks/bans; the second is Dire's.
        # Each block contains div.pick / div.ban children.
        # We only process direct children of picks-inline to avoid double-counting.
        blocks = tree.css("div.picks-inline, [class*=picks-inline]")
        side_order = ["radiant", "dire"]  # first block = radiant, second = dire

        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()  # (action, hero) dedup within this game
        heroes_by_team: dict[str, list[str]] = {}
        sequence = 1

        if blocks:
            # The page duplicates the draft section across responsive-tab variants.
            # Only the first two distinct blocks contain unique content.
            seen_block_heroes: set[str] = set()  # hero-level dedup across blocks
            used_blocks = 0
            for block in blocks:
                # Each block contains all 12 heroes; if we've already seen this set, skip.
                block_heroes = frozenset(
                    img.attributes.get("alt", "")
                    for img in block.css("img[alt]")
                    if img.attributes.get("alt")
                )
                # Skip duplicated responsive copies of same block
                block_key = tuple(sorted(block_heroes))
                if block_key in seen_block_heroes:  # type: ignore[arg-type]
                    continue
                seen_block_heroes.add(block_key)  # type: ignore[arg-type]
                block_side = side_order[used_blocks] if used_blocks < len(side_order) else None
                block_team = radiant if block_side == "radiant" else dire if block_side == "dire" else None
                used_blocks += 1
                for node in block.css("div.pick, div.ban"):
                    klass = node.attributes.get("class", "").lower()
                    text = clean_text(node.text()) or ""
                    if "pick" in klass:
                        action = "pick"
                    elif "ban" in klass:
                        action = "ban"
                    else:
                        continue
                    hero = self._hero_from_node(node) or self._hero_from_text(text)
                    if not hero:
                        continue
                    key = (action, hero)
                    if key in seen:
                        continue
                    seen.add(key)
                    team = block_team
                    if team and action == "pick":
                        heroes_by_team.setdefault(team, []).append(hero)
                    rows.append(
                        {
                            "source": Source.DOTABUFF.value,
                            "source_id": stable_id("draft-pick", match_source_id, sequence, action, hero),
                            "draft_id": None,
                            "game_id": None,
                            "sequence_index": sequence,
                            "phase": self._draft_phase(sequence),
                            "action": action,
                            "team_name": team,
                            "side": block_side,
                            "hero_name": hero,
                            "draft_position": sequence,
                            "is_first_pick": 1 if sequence == 1 and action == "pick" else 0,
                            "is_counter_pick": 1 if action == "pick" and sequence > 1 else 0,
                            "hero_patch_win_rate": parse_pct(self._data_attr(node, "win-rate")),
                            "hero_side_win_rate": parse_pct(self._data_attr(node, "side-win-rate")),
                            "hero_role_win_rate": parse_pct(self._data_attr(node, "role-win-rate")),
                            "_game_source_id": match_source_id,
                            "_draft_source_id": draft_source_id,
                            "raw_json": json_dumps(
                                {"text": text, "class": klass, "draft_source_id": draft_source_id}
                            ),
                        }
                    )
                    sequence += 1
                if used_blocks >= 2:
                    break  # We have both radiant and dire blocks; stop.
        else:
            # Fallback: original broad selector logic with dedup
            candidates = tree.css("[class*=draft] li, [class*=draft] div, .pick, .ban")
            for node in candidates:
                text = clean_text(node.text()) or ""
                klass = node.attributes.get("class", "").lower()
                action = "ban" if "ban" in klass else "pick" if "pick" in klass else None
                hero = self._hero_from_node(node) or self._hero_from_text(text)
                if not action or not hero:
                    continue
                key = (action, hero)
                if key in seen:
                    continue
                seen.add(key)
                side = side_from_text(text) or side_from_text(klass)
                team = radiant if side == "radiant" else dire if side == "dire" else self._team_from_draft_text(text)
                if team and action == "pick":
                    heroes_by_team.setdefault(team, []).append(hero)
                rows.append(
                    {
                        "source": Source.DOTABUFF.value,
                        "source_id": stable_id("draft-pick", match_source_id, sequence, action, hero),
                        "draft_id": None,
                        "game_id": None,
                        "sequence_index": sequence,
                        "phase": self._draft_phase(sequence),
                        "action": action,
                        "team_name": team,
                        "side": side,
                        "hero_name": hero,
                        "draft_position": sequence,
                        "is_first_pick": 1 if sequence == 1 and action == "pick" else 0,
                        "is_counter_pick": 1 if action == "pick" and sequence > 1 else 0,
                        "hero_patch_win_rate": parse_pct(self._data_attr(node, "win-rate")),
                        "hero_side_win_rate": parse_pct(self._data_attr(node, "side-win-rate")),
                        "hero_role_win_rate": parse_pct(self._data_attr(node, "role-win-rate")),
                        "_game_source_id": match_source_id,
                        "_draft_source_id": draft_source_id,
                        "raw_json": json_dumps(
                            {"text": text, "class": klass, "draft_source_id": draft_source_id}
                        ),
                    }
                )
                sequence += 1

        for row in rows:
            team = row.get("team_name")
            hero = row.get("hero_name")
            if team and hero and row.get("action") == "pick":
                partners = [other for other in heroes_by_team.get(team, []) if other != hero]
                row["synergy_pairs_json"] = json_dumps([[hero, partner] for partner in partners])
        return rows

    def _parse_player_stats(self, tree: HTMLParser, match_source_id: str) -> list[dict[str, Any]]:
        stats: list[dict[str, Any]] = []
        seen_players: set[tuple[str | None, str | None]] = set()  # (ign, hero) dedup
        for table_index, table in enumerate(tree.css("table"), start=1):
            headers = [clean_text(th.text()) or "" for th in table.css("thead th, tr th")]
            if not headers:
                continue
            lower_headers = " ".join(headers).lower()
            if not any(token in lower_headers for token in ("kda", "gpm", "xpm", "hero damage", "lh")):
                continue
            for row_index, row in enumerate(table.css("tbody tr, tr"), start=1):
                cells = [clean_text(cell.text()) for cell in row.css("td")]
                if len(cells) < 3:
                    continue
                mapped = self._map_table_row(headers, cells)
                player = self._first_cell_link_text(row) or mapped.get("player") or cells[0]
                hero = self._hero_from_node(row) or mapped.get("hero")
                # Skip rows that don't represent real players
                # (lane-outcome summary rows, totals, etc. have no player link)
                if not self._first_cell_link_text(row) and not hero:
                    continue
                # Deduplicate: same (ign, hero) pair already added from another table copy
                player_key = (player, hero)
                if player_key in seen_players:
                    continue
                seen_players.add(player_key)
                kda = self._kda(mapped)
                stats.append(
                    {
                        "source": Source.DOTABUFF.value,
                        "source_id": stable_id("player-stat", match_source_id, table_index, row_index, player, hero),
                        "player_ign": player,
                        "team_name": mapped.get("team"),
                        "hero_name": hero,
                        "position": parse_int(mapped.get("position") or mapped.get("role")),
                        "lane_assignment": mapped.get("lane"),
                        "kills": parse_int(mapped.get("k")),
                        "deaths": parse_int(mapped.get("d")),
                        "assists": parse_int(mapped.get("a")),
                        "kda": kda,
                        "kill_participation_pct": parse_pct(mapped.get("kp") or mapped.get("kill participation")),
                        "gpm": parse_int(mapped.get("gpm")),
                        "xpm": parse_int(mapped.get("xpm")),
                        "net_worth_end": parse_int(mapped.get("net worth") or mapped.get("nw")),
                        "net_worth_vs_opposing_position": parse_int(mapped.get("net worth diff")),
                        "last_hits": parse_int(mapped.get("lh") or mapped.get("last hits")),
                        "denies": parse_int(mapped.get("dn") or mapped.get("denies")),
                        "hero_damage": parse_int(mapped.get("hero damage")),
                        "tower_damage": parse_int(mapped.get("tower damage")),
                        "hero_healing": parse_int(mapped.get("healing") or mapped.get("hero healing")),
                        "observer_wards_placed": parse_int(mapped.get("obs placed")),
                        "observer_wards_destroyed": parse_int(mapped.get("obs destroyed")),
                        "sentry_wards_placed": parse_int(mapped.get("sen placed")),
                        "sentry_wards_destroyed": parse_int(mapped.get("sen destroyed")),
                        "camps_stacked": parse_int(mapped.get("camps stacked") or mapped.get("stacks")),
                        "ancient_stacks": parse_int(mapped.get("ancient stacks")),
                        "teamfight_participation_pct": parse_pct(mapped.get("teamfight participation")),
                        "final_items_json": json_dumps(self._items_from_row(row, "item")),
                        "backpack_items_json": json_dumps(self._items_from_row(row, "backpack")),
                        "neutral_item": self._neutral_item_from_row(row),
                        "skill_build_json": json_dumps(self._skill_build_from_row(row)),
                        "_game_source_id": match_source_id,
                        "raw_json": json_dumps(mapped),
                    }
                )
        return stats

    def _parse_timeline(self, tree: HTMLParser, match_source_id: str, page_text: str) -> dict[str, Any]:
        timeline_text = " ".join(
            clean_text(node.text()) or "" for node in tree.css("[class*=timeline], [class*=graph]")
        )
        text = f"{page_text} {timeline_text}"
        return {
            "source": Source.DOTABUFF.value,
            "source_id": stable_id("timeline", match_source_id),
            "gold_advantage_10": self._advantage_at(text, "gold", 10),
            "gold_advantage_15": self._advantage_at(text, "gold", 15),
            "gold_advantage_20": self._advantage_at(text, "gold", 20),
            "xp_advantage_10": self._advantage_at(text, "xp", 10),
            "xp_advantage_15": self._advantage_at(text, "xp", 15),
            "xp_advantage_20": self._advantage_at(text, "xp", 20),
            "roshan_kills_json": json_dumps(self._event_times(text, "Roshan")),
            "first_blood_json": json_dumps(self._first_blood(text)),
            "barracks_destroyed_json": json_dumps(self._event_times(text, "Barracks")),
            "tower_kills_json": json_dumps(self._event_times(text, "Tower")),
            "_game_source_id": match_source_id,
            "raw_json": json_dumps({"timeline_text": timeline_text[:5000]}),
        }

    @staticmethod
    def _map_table_row(headers: list[str], cells: list[str | None]) -> dict[str, str | None]:
        mapped: dict[str, str | None] = {}
        for header, cell in zip(headers, cells, strict=False):
            key = (header or "").strip().lower()
            if key:
                mapped[key] = cell
        aliases = {"hero / player": "player", "player hero": "player", "net": "net worth"}
        for src, dest in aliases.items():
            if src in mapped and dest not in mapped:
                mapped[dest] = mapped[src]
        return mapped

    @staticmethod
    def _kda(mapped: dict[str, str | None]) -> float | None:
        value = mapped.get("kda")
        if value:
            return parse_float(value)
        kills = parse_int(mapped.get("k")) or 0
        deaths = parse_int(mapped.get("d")) or 0
        assists = parse_int(mapped.get("a")) or 0
        if kills or assists or deaths:
            return round((kills + assists) / max(1, deaths), 3)
        return None

    @staticmethod
    def _draft_phase(index: int) -> str:
        if index <= 4:
            return "phase_1_bans"
        if index <= 8:
            return "phase_1_picks"
        if index <= 14:
            return "phase_2_bans"
        if index <= 18:
            return "phase_2_picks"
        return "phase_3"

    @staticmethod
    def _series_format(text: str) -> str | None:
        match = BEST_OF_RE.search(text)
        if not match:
            return None
        number = match.group(1) or match.group(2)
        return f"Bo{number}"

    @staticmethod
    def _score(text: str) -> tuple[int | None, int | None]:
        match = SERIES_SCORE_RE.search(text)
        if not match:
            return None, None
        return int(match.group(1)), int(match.group(2))

    @staticmethod
    def _patch(text: str) -> str | None:
        match = PATCH_RE.search(text)
        return match.group(0) if match else None

    @staticmethod
    def _match_id(url: str | None) -> str | None:
        match = MATCH_RE.search(url or "")
        return match.group(1) if match else None

    @staticmethod
    def _team_id(url: str | None) -> str | None:
        match = TEAM_RE.search(url or "")
        return match.group(1) if match else None

    @staticmethod
    def _page_title(tree: HTMLParser) -> str | None:
        for selector in ("h1", "title"):
            node = tree.css_first(selector)
            if node:
                text = clean_text(node.text())
                if text:
                    return text.replace(" - DOTABUFF - Dota 2 Stats", "")
        return None

    def _team_names_from_node(self, node: Node) -> list[str]:
        names: list[str] = []
        for link in node.css('a[href*="/esports/teams/"]'):
            name = clean_text(link.text())
            if name and name not in names:
                names.append(name)
        return names

    def _side_assignments(self, tree: HTMLParser, teams: list[str]) -> tuple[str | None, str | None]:
        radiant = self._nearest_text(tree.root, [".radiant .team-text", "[class*=radiant] [class*=team]"])
        dire = self._nearest_text(tree.root, [".dire .team-text", "[class*=dire] [class*=team]"])
        return radiant or (teams[0] if teams else None), dire or (teams[1] if len(teams) > 1 else None)

    @staticmethod
    def _nearest_text(node: Node, selectors: list[str]) -> str | None:
        for selector in selectors:
            found = node.css_first(selector)
            if found:
                text = clean_text(found.text())
                if text:
                    return text
        return None

    @staticmethod
    def _first_href(node: Node, pattern: re.Pattern[str]) -> str | None:
        for link in node.css("a[href]"):
            href = link.attributes.get("href", "")
            if pattern.search(href):
                return href
        return None

    @staticmethod
    def _hero_from_node(node: Node) -> str | None:
        for image in node.css("img[alt]"):
            alt = clean_text(image.attributes.get("alt"))
            if alt and alt.lower() not in {"radiant", "dire"}:
                return alt
        for link in node.css("a[href]"):
            href = link.attributes.get("href", "")
            match = HERO_RE.search(href)
            if match:
                return match.group(1).replace("-", " ").title()
        return None

    @staticmethod
    def _hero_from_text(text: str) -> str | None:
        match = PICKBAN_HERO_RE.search(text)
        return clean_text(match.group(1)) if match else None

    @staticmethod
    def _data_attr(node: Node, suffix: str) -> str | None:
        for key, value in node.attributes.items():
            if key.endswith(suffix):
                return value
        return None

    @staticmethod
    def _team_from_draft_text(text: str) -> str | None:
        match = PICKBAN_ALT_RE.search(text)
        return clean_text(match.group(1)) if match else None

    @staticmethod
    def _first_pick_team(tree: HTMLParser) -> str | None:
        first = tree.css_first("[class*=draft] .pick, [class*=draft] [class*=pick]")
        return clean_text(first.text()) if first else None

    @staticmethod
    def _winning_side(tree: HTMLParser, text: str) -> str | None:
        # Dotabuff renders e.g. <div class="match-result team radiant">Team Spirit Victory!</div>
        # The winning side is encoded in the class name of .match-result
        for node in tree.css(".match-result"):
            klass = node.attributes.get("class", "").lower()
            node_text = (node.text() or "").lower()
            if "radiant" in klass or "radiant victory" in node_text:
                return "radiant"
            if "dire" in klass or "dire victory" in node_text:
                return "dire"
        # Fallback: plain text scan
        if RADIANT_VICTORY_RE.search(text):
            return "radiant"
        if DIRE_VICTORY_RE.search(text):
            return "dire"
        return None

    @staticmethod
    def _game_number(tree: HTMLParser, text: str) -> int | None:
        # Header text looks like: "Best of 1Team Spirit 1Tundra 0Game 149:43"
        # "Game" is preceded by the score digit "0", so \b fails. Use plain search.
        # The digits after "Game" may be "149" where "1" = game number, "49" = minutes.
        def _extract(raw_text: str) -> int | None:
            m = GAME_NUMBER_RE.search(raw_text)
            if not m:
                return None
            raw = m.group(1)
            # Heuristic: if the captured digits are immediately followed by a time
            # pattern (e.g. "49:43"), the first digit is the game number and the
            # rest is minutes. Otherwise trust the full number as the game number.
            after = raw_text[m.end() : m.end() + 6]
            if len(raw) >= 2 and TIME_RE.search(after):
                return int(raw[0])
            return int(raw)

        for sel in (".match-series-header", "[class*=series-header]"):
            node = tree.css_first(sel)
            if node:
                result = _extract(node.text(deep=True) or "")
                if result is not None:
                    return result
        return _extract(text)

    @staticmethod
    def _mega_creeps(text: str) -> str | None:
        match = MEGA_CREEPS_RE.search(text)
        return match.group(1).title() if match else None

    @staticmethod
    def _items_from_row(row: Node, class_token: str) -> list[str]:
        items: list[str] = []
        for image in row.css(f"[class*={class_token}] img[alt]"):
            alt = clean_text(image.attributes.get("alt"))
            if alt and alt not in items:
                items.append(alt)
        return items[:9]

    @staticmethod
    def _neutral_item_from_row(row: Node) -> str | None:
        node = row.css_first("[class*=neutral] img[alt]")
        return clean_text(node.attributes.get("alt")) if node else None

    @staticmethod
    def _skill_build_from_row(row: Node) -> list[str]:
        skills: list[str] = []
        for node in row.css("[class*=skill] img[alt], [class*=ability] img[alt]"):
            alt = clean_text(node.attributes.get("alt"))
            if alt:
                skills.append(alt)
        return skills

    @staticmethod
    def _first_cell_link_text(row: Node) -> str | None:
        # Prefer an /esports/players/ link (contains the actual IGN, not a number)
        for link in row.css('td a[href*="/esports/players/"], td a[href*="/players/"]'):
            name = clean_text(link.text())
            if name:  # skip empty <a> elements (icons etc.)
                return name
        # Fallback: first non-empty <a> in a <td>
        for link in row.css("td a"):
            name = clean_text(link.text())
            if name:
                return name
        return None

    @staticmethod
    def _advantage_at(text: str, metric: str, minute: int) -> int | None:
        pattern = rf"{metric}\D{{0,30}}{minute}\D{{0,20}}(-?\d[\d,]*)"
        match = re.search(pattern, text, re.I)
        return parse_int(match.group(1)) if match else None

    @staticmethod
    def _event_times(text: str, label: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for match in re.finditer(rf"{label}[^\d]{{0,30}}(\d{{1,2}}:\d{{2}})", text, re.I):
            events.append({"event": label, "time": match.group(1)})
        return events

    @staticmethod
    def _first_blood(text: str) -> dict[str, Any] | None:
        match = FIRST_BLOOD_RE.search(text)
        if not match:
            return None
        return {"time": match.group(1)}

    @staticmethod
    def _key_value_text(tree: HTMLParser) -> dict[str, str]:
        stats: dict[str, str] = {}
        for row in tree.css("tr, .stat, [class*=stat]"):
            cells = [clean_text(cell.text()) for cell in row.css("th, td, .label, .value")]
            cells = [cell for cell in cells if cell]
            if len(cells) >= 2:
                stats[cells[0].lower()] = cells[1]
        return stats

