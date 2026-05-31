from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag
from loguru import logger

from dota2_scraper.models import FetchJob, FetchedPage, PageKind, ParsedPayload, Source
from dota2_scraper.utils import (
    absolute_url,
    clean_text,
    detect_region,
    json_dumps,
    parse_float,
    parse_int,
    parse_money,
    stable_id,
)

REGIONS = {"WEU", "EEU", "NA", "SA", "CN", "SEA", "MENA"}


class LiquipediaParser:
    """Defensive Liquipedia parser for tournaments, schedules, rosters, transfers, and earnings."""

    def __init__(self, base_url: str = "https://liquipedia.net") -> None:
        self.base_url = base_url.rstrip("/")

    def parse(self, page: FetchedPage) -> ParsedPayload:
        soup = BeautifulSoup(page.html, "html.parser")
        payload = ParsedPayload(source=Source.LIQUIPEDIA, url=page.final_url)
        self._discover_links(soup, payload, page.job.depth)
        try:
            if "Portal:" in page.final_url or page.job.kind == PageKind.LIQUIPEDIA_PORTAL:
                self._parse_portal_page(soup, page, payload)
            elif self._looks_like_team(page.final_url, soup):
                self._parse_team_page(soup, page, payload)
            elif self._looks_like_player(page.final_url, soup):
                self._parse_player_page(soup, page, payload)
            elif self._looks_like_tournament(page.final_url, soup):
                self._parse_tournament_page(soup, page, payload)
            else:
                self._parse_portal_page(soup, page, payload)
        except Exception as exc:
            logger.exception("Liquipedia parser recovered from {} on {}", exc, page.final_url)
        return payload

    def _discover_links(self, soup: BeautifulSoup, payload: ParsedPayload, depth: int) -> None:
        if depth >= 2:
            return
        seen: set[str] = set()
        for link in soup.select("a[href]"):
            href = link.get("href")
            if not href or not href.startswith("/dota2/"):
                continue
            if any(skip in href for skip in ("/Special:", "action=", "#", "/File:", "/Template:")):
                continue
            url = absolute_url(self.base_url, href)
            if not url or url in seen:
                continue
            seen.add(url)
            text = clean_text(link.get_text(" ")) or ""
            kind = self._kind_from_link(href, text)
            if kind != PageKind.UNKNOWN:
                payload.discovered_jobs.append(
                    FetchJob(url=url, source=Source.LIQUIPEDIA, kind=kind, depth=depth + 1)
                )

    def _parse_portal_page(
        self, soup: BeautifulSoup, page: FetchedPage, payload: ParsedPayload
    ) -> None:
        self._parse_match_tables(soup, page, payload)
        for table in soup.select("table"):
            headers = self._headers(table)
            lower = " ".join(headers).lower()
            if "prize" in lower or "tier" in lower or "event" in lower:
                for row_index, mapped in enumerate(self._table_dicts(table), start=1):
                    name = mapped.get("event") or mapped.get("tournament") or mapped.get("name")
                    if not name:
                        continue
                    payload.add(
                        "tournaments",
                        {
                            "source": Source.LIQUIPEDIA.value,
                            "source_id": stable_id("liquipedia-tournament-list", page.final_url, row_index, name),
                            "name": name,
                            "tier": self._tier(mapped.get("tier")),
                            "region": detect_region(" ".join(mapped.values())),
                            "prize_pool_total": parse_money(mapped.get("prize pool") or mapped.get("prize")),
                            "start_date": mapped.get("start date") or mapped.get("date"),
                            "end_date": mapped.get("end date"),
                            "raw_json": json_dumps(mapped),
                        },
                    )

    def _parse_tournament_page(
        self, soup: BeautifulSoup, page: FetchedPage, payload: ParsedPayload
    ) -> None:
        title = self._title(soup) or page.final_url.rsplit("/", 1)[-1].replace("_", " ")
        infobox = self._infobox(soup)
        tournament_id = stable_id("liquipedia-tournament", page.final_url)
        payload.add(
            "tournaments",
            {
                "source": Source.LIQUIPEDIA.value,
                "source_id": tournament_id,
                "name": title,
                "tier": self._tier(infobox.get("tier") or infobox.get("liquipediatier")),
                "region": detect_region(infobox.get("region") or infobox.get("location") or title),
                "prize_pool_total": parse_money(infobox.get("prize pool") or infobox.get("prizepool")),
                "prize_pool_breakdown_json": json_dumps(self._placement_prizes(soup)),
                "dpc_points_json": json_dumps(self._dpc_points(soup)),
                "start_date": infobox.get("start date") or infobox.get("date start") or infobox.get("dates"),
                "end_date": infobox.get("end date") or infobox.get("date end"),
                "raw_json": json_dumps({"infobox": infobox, "url": page.final_url}),
            },
        )
        self._parse_match_tables(soup, page, payload, tournament_name=title)
        self._parse_stage_tables(soup, page, payload, tournament_name=title)
        self._parse_earnings_tables(soup, page, payload, tournament_name=title)

    def _parse_team_page(self, soup: BeautifulSoup, page: FetchedPage, payload: ParsedPayload) -> None:
        title = self._title(soup) or page.final_url.rsplit("/", 1)[-1].replace("_", " ")
        infobox = self._infobox(soup)
        team_id = stable_id("liquipedia-team", page.final_url)
        payload.add(
            "teams",
            {
                "source": Source.LIQUIPEDIA.value,
                "source_id": team_id,
                "name": title,
                "region": detect_region(infobox.get("region") or infobox.get("location") or title),
                "total_prize_money": parse_money(infobox.get("total winnings") or infobox.get("earnings")),
                "raw_json": json_dumps({"infobox": infobox, "url": page.final_url}),
            },
        )
        self._parse_rosters(soup, payload, title, team_id)
        self._parse_staff(soup, payload, title, team_id)
        self._parse_transfer_history(soup, payload, title, team_id)
        self._parse_team_earnings(soup, payload, title, team_id)

    def _parse_player_page(self, soup: BeautifulSoup, page: FetchedPage, payload: ParsedPayload) -> None:
        title = self._title(soup) or page.final_url.rsplit("/", 1)[-1].replace("_", " ")
        infobox = self._infobox(soup)
        player_id = stable_id("liquipedia-player", page.final_url)
        payload.add(
            "players",
            {
                "source": Source.LIQUIPEDIA.value,
                "source_id": player_id,
                "ign": title,
                "real_name": infobox.get("name") or infobox.get("real name"),
                "nationality": infobox.get("nationality") or infobox.get("country"),
                "primary_position": parse_int(infobox.get("position") or infobox.get("role")),
                "career_earnings": parse_money(infobox.get("total winnings") or infobox.get("earnings")),
                "raw_json": json_dumps({"infobox": infobox, "url": page.final_url}),
            },
        )
        self._parse_player_earnings(soup, payload, title, player_id)
        self._parse_player_transfers(soup, payload, title, player_id)

    def _parse_match_tables(
        self,
        soup: BeautifulSoup,
        page: FetchedPage,
        payload: ParsedPayload,
        tournament_name: str | None = None,
    ) -> None:
        for idx, container in enumerate(soup.select(".match, .match-row, .brkts-match, .wikitable tr"), start=1):
            text = clean_text(container.get_text(" ")) or ""
            if not text or not self._has_match_signal(text):
                continue
            teams = self._teams_from_match(container)
            score = self._score(text)
            payload.add(
                "matches",
                {
                    "source": Source.LIQUIPEDIA.value,
                    "source_id": stable_id("liquipedia-match", page.final_url, idx, text[:80]),
                    "tournament_name": tournament_name,
                    "region": detect_region(text),
                    "team_a_name": teams[0] if len(teams) > 0 else None,
                    "team_b_name": teams[1] if len(teams) > 1 else None,
                    "team_a_score": score[0],
                    "team_b_score": score[1],
                    "series_format": self._series_format(text),
                    "scheduled_at_utc": self._datetime_utc(container),
                    "status": "completed" if score != (None, None) else "scheduled",
                    "head_to_head_all_time": self._h2h_text(container),
                    "head_to_head_by_tier_json": json_dumps(self._h2h_by_tier(container)),
                    "raw_json": json_dumps({"text": text}),
                },
            )

    def _parse_stage_tables(
        self,
        soup: BeautifulSoup,
        page: FetchedPage,
        payload: ParsedPayload,
        tournament_name: str,
    ) -> None:
        for heading in soup.select("h2, h3"):
            name = clean_text(heading.get_text(" "))
            if not name or not re.search(r"group|playoff|stage|bracket|round robin|gsl", name, re.I):
                continue
            payload.add(
                "tournaments",
                {
                    "source": Source.LIQUIPEDIA.value,
                    "source_id": stable_id("liquipedia-stage", page.final_url, name),
                    "name": f"{tournament_name} - {name}",
                    "raw_json": json_dumps({"stage_name": name, "stage_format": self._stage_format(name)}),
                },
            )

    def _parse_rosters(
        self, soup: BeautifulSoup, payload: ParsedPayload, team_name: str, team_source_id: str
    ) -> None:
        for table in soup.select("table"):
            headers = self._headers(table)
            lower = " ".join(headers).lower()
            if not any(token in lower for token in ("id", "player", "position", "name")):
                continue
            section = self._previous_heading(table)
            is_roster = section and re.search(r"active|roster|players|current", section, re.I)
            if not is_roster:
                continue
            for row_index, mapped in enumerate(self._table_dicts(table), start=1):
                ign = mapped.get("id") or mapped.get("player") or mapped.get("ign")
                if not ign:
                    continue
                player_id = stable_id("liquipedia-player", ign)
                payload.add(
                    "players",
                    {
                        "source": Source.LIQUIPEDIA.value,
                        "source_id": player_id,
                        "ign": ign,
                        "real_name": mapped.get("name") or mapped.get("real name"),
                        "nationality": mapped.get("nationality") or mapped.get("country"),
                        "primary_position": parse_int(mapped.get("position") or mapped.get("role")),
                        "raw_json": json_dumps(mapped),
                    },
                )
                payload.add(
                    "rosters",
                    {
                        "source": Source.LIQUIPEDIA.value,
                        "source_id": stable_id("roster", team_source_id, row_index, ign),
                        "team_name": team_name,
                        "player_ign": ign,
                        "real_name": mapped.get("name") or mapped.get("real name"),
                        "position": parse_int(mapped.get("position") or mapped.get("role")),
                        "nationality": mapped.get("nationality") or mapped.get("country"),
                        "join_date": mapped.get("join date") or mapped.get("joined"),
                        "is_active": 1,
                        "raw_json": json_dumps(mapped),
                    },
                )

    def _parse_staff(
        self, soup: BeautifulSoup, payload: ParsedPayload, team_name: str, team_source_id: str
    ) -> None:
        for table in soup.select("table"):
            section = self._previous_heading(table) or ""
            if not re.search(r"coach|staff|analyst", section, re.I):
                continue
            for row_index, mapped in enumerate(self._table_dicts(table), start=1):
                ign = mapped.get("id") or mapped.get("name") or mapped.get("staff")
                if not ign:
                    continue
                payload.add(
                    "staff",
                    {
                        "source": Source.LIQUIPEDIA.value,
                        "source_id": stable_id("staff", team_source_id, row_index, ign),
                        "team_name": team_name,
                        "ign": ign,
                        "real_name": mapped.get("real name"),
                        "role": mapped.get("role") or section,
                        "nationality": mapped.get("nationality") or mapped.get("country"),
                        "join_date": mapped.get("join date") or mapped.get("joined"),
                        "raw_json": json_dumps(mapped),
                    },
                )

    def _parse_transfer_history(
        self, soup: BeautifulSoup, payload: ParsedPayload, team_name: str, team_source_id: str
    ) -> None:
        for table in soup.select("table"):
            section = self._previous_heading(table) or ""
            lower = " ".join(self._headers(table)).lower() + " " + section.lower()
            if not re.search(r"transfer|stand-?in|loan", lower):
                continue
            for row_index, mapped in enumerate(self._table_dicts(table), start=1):
                player = mapped.get("player") or mapped.get("id") or mapped.get("name")
                if not player:
                    continue
                move_type = self._move_type(" ".join(mapped.values()))
                target_table = "standins" if move_type == "stand-in" else "rosters"
                row = {
                    "source": Source.LIQUIPEDIA.value,
                    "source_id": stable_id(target_table, team_source_id, row_index, player, move_type),
                    "team_name": team_name,
                    "player_ign": player,
                    "start_date": mapped.get("date") or mapped.get("start date"),
                    "end_date": mapped.get("end date"),
                    "move_type": move_type,
                    "raw_json": json_dumps(mapped),
                }
                if target_table == "standins":
                    row["replaced_player_ign"] = mapped.get("replaces") or mapped.get("replaced")
                    row["reason"] = mapped.get("reason")
                else:
                    row["join_date"] = mapped.get("date") or mapped.get("join date")
                    row["is_active"] = 0
                payload.add(target_table, row)

    def _parse_team_earnings(
        self, soup: BeautifulSoup, payload: ParsedPayload, team_name: str, team_source_id: str
    ) -> None:
        for row_index, mapped in enumerate(self._earning_rows(soup), start=1):
            tournament = mapped.get("tournament") or mapped.get("event")
            amount = parse_money(mapped.get("prize") or mapped.get("prize money") or mapped.get("earnings"))
            if not tournament and amount is None:
                continue
            payload.add(
                "earnings",
                {
                    "source": Source.LIQUIPEDIA.value,
                    "source_id": stable_id("team-earning", team_source_id, row_index, tournament, amount),
                    "team_name": team_name,
                    "tournament_name": tournament,
                    "placement": mapped.get("place") or mapped.get("placement"),
                    "amount": amount,
                    "currency": "USD" if "$" in " ".join(mapped.values()) else None,
                    "earned_at": mapped.get("date"),
                    "is_ti_history": 1 if tournament and "International" in tournament else 0,
                    "raw_json": json_dumps(mapped),
                },
            )

    def _parse_player_earnings(
        self, soup: BeautifulSoup, payload: ParsedPayload, player_ign: str, player_source_id: str
    ) -> None:
        for row_index, mapped in enumerate(self._earning_rows(soup), start=1):
            tournament = mapped.get("tournament") or mapped.get("event")
            amount = parse_money(mapped.get("prize") or mapped.get("earnings") or mapped.get("winnings"))
            if not tournament and amount is None:
                continue
            payload.add(
                "earnings",
                {
                    "source": Source.LIQUIPEDIA.value,
                    "source_id": stable_id("player-earning", player_source_id, row_index, tournament, amount),
                    "player_ign": player_ign,
                    "tournament_name": tournament,
                    "placement": mapped.get("place") or mapped.get("placement"),
                    "amount": amount,
                    "currency": "USD" if "$" in " ".join(mapped.values()) else None,
                    "earned_at": mapped.get("date"),
                    "is_ti_history": 1 if tournament and "International" in tournament else 0,
                    "raw_json": json_dumps(mapped),
                },
            )

    def _parse_player_transfers(
        self, soup: BeautifulSoup, payload: ParsedPayload, player_ign: str, player_source_id: str
    ) -> None:
        for table in soup.select("table"):
            lower = " ".join(self._headers(table)).lower() + " " + (self._previous_heading(table) or "").lower()
            if "transfer" not in lower and "team" not in lower:
                continue
            for row_index, mapped in enumerate(self._table_dicts(table), start=1):
                move_type = self._move_type(" ".join(mapped.values()))
                payload.add(
                    "standins" if move_type == "stand-in" else "rosters",
                    {
                        "source": Source.LIQUIPEDIA.value,
                        "source_id": stable_id("player-transfer", player_source_id, row_index, move_type),
                        "player_ign": player_ign,
                        "team_name": mapped.get("team") or mapped.get("to") or mapped.get("new team"),
                        "join_date": mapped.get("date") if move_type != "stand-in" else None,
                        "start_date": mapped.get("date") if move_type == "stand-in" else None,
                        "move_type": move_type,
                        "raw_json": json_dumps(mapped),
                    },
                )

    def _parse_earnings_tables(
        self, soup: BeautifulSoup, page: FetchedPage, payload: ParsedPayload, tournament_name: str
    ) -> None:
        for row_index, mapped in enumerate(self._earning_rows(soup), start=1):
            team = mapped.get("team") or mapped.get("participant")
            amount = parse_money(mapped.get("prize") or mapped.get("prize money"))
            if amount is None:
                continue
            payload.add(
                "earnings",
                {
                    "source": Source.LIQUIPEDIA.value,
                    "source_id": stable_id("tournament-earning", page.final_url, row_index, team, amount),
                    "team_name": team,
                    "tournament_name": tournament_name,
                    "placement": mapped.get("place") or mapped.get("placement"),
                    "amount": amount,
                    "currency": "USD" if "$" in " ".join(mapped.values()) else None,
                    "is_ti_history": 1 if "International" in tournament_name else 0,
                    "raw_json": json_dumps(mapped),
                },
            )

    @staticmethod
    def _title(soup: BeautifulSoup) -> str | None:
        heading = soup.select_one("h1, .firstHeading")
        return clean_text(heading.get_text(" ")) if heading else None

    @staticmethod
    def _headers(table: Tag) -> list[str]:
        first_row = table.select_one("tr")
        if not first_row:
            return []
        headers = [clean_text(cell.get_text(" ")) or "" for cell in first_row.select("th")]
        if headers:
            return [header.lower() for header in headers]
        return [clean_text(cell.get_text(" ")) or "" for cell in first_row.select("td")]

    def _table_dicts(self, table: Tag) -> list[dict[str, str]]:
        headers = self._headers(table)
        rows: list[dict[str, str]] = []
        for tr in table.select("tr")[1:]:
            cells = [clean_text(td.get_text(" ")) or "" for td in tr.select("td")]
            if not cells:
                continue
            if headers and len(headers) <= len(cells):
                rows.append({headers[i].lower(): cells[i] for i in range(min(len(headers), len(cells)))})
            else:
                rows.append({f"col_{i + 1}": cell for i, cell in enumerate(cells)})
        return rows

    @staticmethod
    def _infobox(soup: BeautifulSoup) -> dict[str, str]:
        info: dict[str, str] = {}
        for row in soup.select(".infobox tr, .fo-nttax-infobox tr, .wiki-bordercolor-light tr"):
            cells = row.select("th, td")
            if len(cells) < 2:
                continue
            key = clean_text(cells[0].get_text(" "))
            value = clean_text(cells[1].get_text(" "))
            if key and value:
                info[key.lower().replace(":", "")] = value
        return info

    @staticmethod
    def _previous_heading(node: Tag) -> str | None:
        previous = node.find_previous(["h2", "h3", "h4"])
        return clean_text(previous.get_text(" ")) if previous else None

    @staticmethod
    def _kind_from_link(href: str, text: str) -> PageKind:
        if any(portal in href for portal in ("Portal:", "Liquipedia:Upcoming", "Liquipedia:Matches")):
            return PageKind.LIQUIPEDIA_PORTAL
        if re.search(r"team|roster", text, re.I):
            return PageKind.LIQUIPEDIA_TEAM
        if re.search(r"tournament|major|league|cup|international|dreamleague|blast", text, re.I):
            return PageKind.LIQUIPEDIA_TOURNAMENT
        return PageKind.LIQUIPEDIA_PLAYER if re.match(r"/dota2/[A-Za-z0-9_%-]+$", href) else PageKind.UNKNOWN

    @staticmethod
    def _looks_like_team(url: str, soup: BeautifulSoup) -> bool:
        text = clean_text(soup.get_text(" ")) or ""
        return "/Team" in url or bool(re.search(r"Current roster|Active Squad|Former Players", text, re.I))

    @staticmethod
    def _looks_like_player(url: str, soup: BeautifulSoup) -> bool:
        text = clean_text(soup.get_text(" ")) or ""
        return bool(re.search(r"Approx\. Total Winnings|Player Information|Signature Heroes", text, re.I))

    @staticmethod
    def _looks_like_tournament(url: str, soup: BeautifulSoup) -> bool:
        text = clean_text(soup.get_text(" ")) or ""
        return bool(re.search(r"Prize Pool|Participants|Group Stage|Playoffs|DPC Points", text, re.I))

    @staticmethod
    def _tier(value: str | None) -> int | None:
        text = clean_text(value)
        if not text:
            return None
        if re.search(r"\bS\b|tier\s*1|premier", text, re.I):
            return 1
        if re.search(r"\bA\b|tier\s*2|major", text, re.I):
            return 2
        if re.search(r"\bB\b|tier\s*3|minor", text, re.I):
            return 3
        return parse_int(text)

    @staticmethod
    def _score(text: str) -> tuple[int | None, int | None]:
        match = re.search(r"\b(\d+)\s*[-:]\s*(\d+)\b", text)
        if not match:
            return None, None
        return int(match.group(1)), int(match.group(2))

    @staticmethod
    def _series_format(text: str) -> str | None:
        match = re.search(r"\bBo\s?([1253])\b|Best of\s+(\d)", text, re.I)
        if not match:
            return None
        return f"Bo{match.group(1) or match.group(2)}"

    @staticmethod
    def _has_match_signal(text: str) -> bool:
        return bool(re.search(r"\bvs\b|\d+\s*[-:]\s*\d+|Bo[1253]|Match", text, re.I))

    @staticmethod
    def _teams_from_match(container: Tag) -> list[str]:
        teams: list[str] = []
        selectors = ".team-template-text a, .team-template-team-standard a, .brkts-opponent-entry a, a"
        for link in container.select(selectors):
            text = clean_text(link.get_text(" "))
            href = link.get("href") or ""
            if text and "/dota2/" in href and text not in teams and not text.isdigit():
                teams.append(text)
            if len(teams) >= 2:
                break
        return teams

    @staticmethod
    def _datetime_utc(container: Tag) -> str | None:
        countdown = container.select_one(".timer-object, [data-timestamp]")
        if countdown and countdown.get("data-timestamp"):
            return countdown.get("data-timestamp")
        return None

    @staticmethod
    def _h2h_text(container: Tag) -> str | None:
        text = clean_text(container.get_text(" ")) or ""
        match = re.search(r"H2H.{0,80}", text, re.I)
        return clean_text(match.group(0)) if match else None

    @staticmethod
    def _h2h_by_tier(container: Tag) -> dict[str, Any] | None:
        text = clean_text(container.get_text(" ")) or ""
        if "H2H" not in text.upper():
            return None
        return {"raw": text[:500]}

    @staticmethod
    def _stage_format(text: str) -> str | None:
        if re.search(r"round robin", text, re.I):
            return "round robin"
        if re.search(r"gsl", text, re.I):
            return "GSL"
        if re.search(r"double", text, re.I):
            return "double elimination"
        if re.search(r"single", text, re.I):
            return "single elimination"
        return None

    def _placement_prizes(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        rows = []
        for table in soup.select("table"):
            headers = " ".join(self._headers(table)).lower()
            if "prize" not in headers or not re.search(r"place|placement", headers):
                continue
            rows.extend(self._table_dicts(table))
        return rows

    def _dpc_points(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        rows = []
        for table in soup.select("table"):
            headers = " ".join(self._headers(table)).lower()
            if "dpc" in headers or "points" in headers:
                rows.extend(self._table_dicts(table))
        return rows

    def _earning_rows(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        rows = []
        for table in soup.select("table"):
            headers = " ".join(self._headers(table)).lower()
            if any(token in headers for token in ("prize", "earnings", "winnings")):
                rows.extend(self._table_dicts(table))
        return rows

    @staticmethod
    def _move_type(text: str) -> str:
        if re.search(r"stand-?in", text, re.I):
            return "stand-in"
        if re.search(r"loan", text, re.I):
            return "loan"
        return "permanent"
