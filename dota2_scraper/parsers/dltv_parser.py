from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup, Tag

from dota2_scraper.models import Source
from dota2_scraper.utils import clean_text, json_dumps, stable_id

MATCH_ID_RE = re.compile(r"/matches/(\d+)")
SCORE_RE = re.compile(r"\b(\d+)\s*[-:]\s*(\d+)\b")
FORMAT_RE = re.compile(r"\bbo\s*([1-9])\b", re.I)
POINTS_RE = re.compile(r"(\d[\d,]*)\s*pts\.?", re.I)
RANK_RE = re.compile(r"^(\d{1,2})")
DELTA_RE = re.compile(r"([+-]\d+)")


class DltvParser:
    """Parses DLTV internal JSON + HTML fallback payloads into DB row dicts."""

    def __init__(self, base_url: str = "https://dltv.org") -> None:
        self.base_url = base_url.rstrip("/")

    def parse_config(self, config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        rows: dict[str, list[dict[str, Any]]] = {
            "teams": [],
            "tournaments": [],
            "matches": [],
        }
        featured_series: list[dict[str, Any]] = []
        for platform in config.get("platforms") or []:
            if not isinstance(platform, dict):
                continue
            featured_series.extend([series for series in (platform.get("featured_series") or []) if isinstance(series, dict)])

        seen_match_ids: set[str] = set()
        for series in featured_series:
            series_id = series.get("id")
            if series_id is None:
                continue
            source_id = str(series_id)
            if source_id in seen_match_ids:
                continue
            seen_match_ids.add(source_id)

            event = series.get("event") or {}
            first_team = series.get("first_team") or {}
            second_team = series.get("second_team") or {}

            team_a = clean_text(first_team.get("title") or first_team.get("tag"))
            team_b = clean_text(second_team.get("title") or second_team.get("tag"))
            tournament_name = clean_text(event.get("title") or event.get("tag"))

            rows["teams"].extend(self._team_rows_from_series(series))
            tournament_row = self._tournament_row_from_event(event)
            if tournament_row:
                rows["tournaments"].append(tournament_row)

            started_at = self._normalize_timestamp(series.get("started_at") or series.get("liquipedia_date"))
            completed_at = self._normalize_timestamp(series.get("ended_at")) or started_at
            status = self._status_from_series(series)
            series_format = self._series_format_from_text(clean_text(series.get("slug")) or "")
            if not series_format:
                bo_value = clean_text(series.get("bo"))
                if bo_value and bo_value.isdigit():
                    series_format = f"Bo{bo_value}"

            team_a_score, team_b_score = self._series_score_from_series(series)

            rows["matches"].append(
                {
                    "source": Source.DLTV.value,
                    "source_id": source_id,
                    "team_a_name": team_a,
                    "team_b_name": team_b,
                    "team_a_score": team_a_score,
                    "team_b_score": team_b_score,
                    "series_format": series_format,
                    "tournament_name": tournament_name,
                    "scheduled_at_utc": started_at,
                    "completed_at_utc": completed_at if status == "completed" else None,
                    "status": status,
                    "raw_json": json_dumps({"source": "config", "series": series}),
                }
            )

        return rows

    def parse_ranking_html(self, html: str, ranking_type: str, fetched_at: str) -> dict[str, list[dict[str, Any]]]:
        soup = BeautifulSoup(html, "html.parser")
        rows: dict[str, list[dict[str, Any]]] = {
            "teams": [],
            "world_rankings": [],
            "ept_rankings": [],
        }

        seen: set[tuple[str, int]] = set()
        for link in soup.select('a[href*="/teams/"]'):
            href = link.get("href") or ""
            team_slug = href.rsplit("/", 1)[-1].strip()
            team_name = clean_text(link.get_text(" "))
            if not team_name:
                continue
            container = link.find_parent(["tr", "li", "div"])
            row_text = clean_text(container.get_text(" ")) if isinstance(container, Tag) else None
            text = row_text or team_name

            rank_position = self._rank_position_from_text(text)
            points = self._points_from_text(text)
            delta = self._rank_delta_from_text(text)
            if rank_position is None or points is None:
                continue

            team_source_id = stable_id("dltv-team", team_slug, team_name)
            rows["teams"].append(
                {
                    "source": Source.DLTV.value,
                    "source_id": team_source_id,
                    "name": team_name,
                    "raw_json": json_dumps({"slug": team_slug}),
                }
            )

            key = (team_name.lower(), rank_position)
            if key in seen:
                continue
            seen.add(key)

            target_table = "ept_rankings" if ranking_type == "ept" else "world_rankings"
            rows[target_table].append(
                {
                    "team_name": team_name,
                    "ept_points": points,
                    "rank_position": rank_position,
                    "rank_delta": delta,
                    "fetched_at": fetched_at,
                }
            )
        return rows

    def parse_transfers_html(self, html: str) -> dict[str, list[dict[str, Any]]]:
        soup = BeautifulSoup(html, "html.parser")
        rows: dict[str, list[dict[str, Any]]] = {"transfers": []}

        candidates = soup.find_all("a", href=re.compile(r"/players/"))
        for player_link in candidates:
            player_name = clean_text(player_link.get_text(" "))
            if not player_name:
                continue

            container = player_link.find_parent(["tr", "li", "div"])
            if container is None:
                continue
            container_text = clean_text(container.get_text(" ")) or ""

            team_links = container.find_all("a", href=re.compile(r"/teams/"))
            if len(team_links) < 2:
                continue

            from_team = clean_text(team_links[0].get_text(" "))
            to_team = clean_text(team_links[1].get_text(" "))
            transfer_date = self._extract_transfer_date(container_text)

            rows["transfers"].append(
                {
                    "player_name": player_name,
                    "from_team": from_team,
                    "to_team": to_team,
                    "transfer_date": transfer_date,
                    "source": Source.DLTV.value,
                }
            )
        return rows

    def parse_results_html(self, html: str) -> dict[str, list[dict[str, Any]]]:
        soup = BeautifulSoup(html, "html.parser")
        rows: dict[str, list[dict[str, Any]]] = {"matches": []}

        seen: set[str] = set()
        for link in soup.select('a[href*="/matches/"]'):
            href = link.get("href") or ""
            match = MATCH_ID_RE.search(href)
            if not match:
                continue
            match_id = match.group(1)
            if match_id in seen:
                continue
            seen.add(match_id)

            container = link.find_parent(["tr", "li", "div", "article"])
            text = clean_text(container.get_text(" ")) if isinstance(container, Tag) else None
            text = text or clean_text(link.get_text(" ")) or ""
            if not text:
                continue
            score = SCORE_RE.search(text)
            if not score:
                continue

            team_a_name, team_b_name = self._teams_from_match_text(text)
            match_dt = self._extract_match_datetime(text)
            rows["matches"].append(
                {
                    "source": Source.DLTV.value,
                    "source_id": match_id,
                    "team_a_name": team_a_name,
                    "team_b_name": team_b_name,
                    "team_a_score": int(score.group(1)),
                    "team_b_score": int(score.group(2)),
                    "series_format": self._series_format_from_text(text),
                    "tournament_name": self._extract_tournament_name(text),
                    "completed_at_utc": match_dt,
                    "status": "completed",
                    "raw_json": json_dumps({"source": "results_html", "text": text}),
                }
            )
        return rows

    @staticmethod
    def _normalize_timestamp(value: Any) -> str | None:
        text = clean_text(value)
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        except ValueError:
            return None

    @staticmethod
    def _status_from_series(series: dict[str, Any]) -> str:
        status_code = series.get("status")
        started_at = series.get("started_at")
        ended_at = series.get("ended_at")
        if ended_at:
            return "completed"
        if status_code in {1, 2, 3}:
            return "live"
        if started_at:
            return "scheduled"
        return "scheduled"

    def _series_score_from_series(self, series: dict[str, Any]) -> tuple[int | None, int | None]:
        first_score = series.get("first_team_score")
        second_score = series.get("second_team_score")
        if first_score is not None and second_score is not None:
            try:
                return int(first_score), int(second_score)
            except (TypeError, ValueError):
                pass

        # Bets do not contain a reliable per-series score; fall back to unknown.
        return (None, None)

    @staticmethod
    def _series_format_from_text(text: str) -> str | None:
        match = FORMAT_RE.search(text)
        if not match:
            return None
        return f"Bo{match.group(1)}"

    def _team_rows_from_series(self, series: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key in ("first_team", "second_team"):
            team = series.get(key) or {}
            if not isinstance(team, dict):
                continue
            team_id = team.get("id")
            team_name = clean_text(team.get("title") or team.get("tag"))
            if team_id is None or not team_name:
                continue
            rows.append(
                {
                    "source": Source.DLTV.value,
                    "source_id": str(team_id),
                    "name": team_name,
                    "region": clean_text(team.get("country") or team.get("country_title")),
                    "raw_json": json_dumps(team),
                }
            )
        return rows

    def _tournament_row_from_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(event, dict):
            return None
        event_id = event.get("id")
        name = clean_text(event.get("title") or event.get("tag"))
        if event_id is None or not name:
            return None
        return {
            "source": Source.DLTV.value,
            "source_id": str(event_id),
            "name": name,
            "tier": self._tier_from_text(clean_text(event.get("tier"))),
            "start_date": self._normalize_timestamp(event.get("started_at")),
            "end_date": self._normalize_timestamp(event.get("ended_at")),
            "raw_json": json_dumps(event),
        }

    @staticmethod
    def _tier_from_text(value: str | None) -> int | None:
        if not value:
            return None
        mapping = {"S": 1, "A": 2, "B": 3, "C": 4}
        return mapping.get(value.upper())

    @staticmethod
    def _rank_position_from_text(text: str) -> int | None:
        match = RANK_RE.search(text)
        if not match:
            return None
        return int(match.group(1))

    @staticmethod
    def _points_from_text(text: str) -> int | None:
        match = POINTS_RE.search(text)
        if not match:
            return None
        return int(match.group(1).replace(",", ""))

    @staticmethod
    def _rank_delta_from_text(text: str) -> int | None:
        match = DELTA_RE.search(text)
        if not match:
            return 0
        return int(match.group(1))

    @staticmethod
    def _teams_from_match_text(text: str) -> tuple[str | None, str | None]:
        score = SCORE_RE.search(text)
        if not score:
            return None, None
        before = clean_text(text[: score.start()])
        after = clean_text(text[score.end() :])
        if after and "bo" in after.lower():
            after = clean_text(re.split(r"\bbo\d+\b", after, flags=re.I)[0])
        return before, after

    @staticmethod
    def _extract_transfer_date(text: str) -> str | None:
        # Accept both exact timestamps and short month-day labels.
        patterns = [
            r"\b(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\b",
            r"\b(\d{4}-\d{2}-\d{2})\b",
            r"\b([A-Z][a-z]{2}\s+\d{1,2})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            value = match.group(1)
            if re.match(r"^[A-Z][a-z]{2}\s+\d{1,2}$", value):
                try:
                    dt = datetime.strptime(f"{value} {datetime.now(timezone.utc).year}", "%b %d %Y")
                    return dt.replace(tzinfo=timezone.utc).isoformat()
                except ValueError:
                    return None
            if " " in value and "T" not in value:
                value = value.replace(" ", "T")
            if len(value) == 10:
                value = value + "T00:00:00"
            try:
                dt = datetime.fromisoformat(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
            except ValueError:
                continue
        return None

    @staticmethod
    def _extract_match_datetime(text: str) -> str | None:
        match = re.search(r"\b(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)\b", text)
        if not match:
            return None
        raw = match.group(1).replace(" ", "T")
        if len(raw) == 16:
            raw = raw + ":00"
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        except ValueError:
            return None

    @staticmethod
    def _extract_tournament_name(text: str) -> str | None:
        if not text:
            return None
        parts = re.split(r"\bbo[1-9]\b", text, flags=re.I)
        candidate = clean_text(parts[-1] if parts else text)
        if not candidate:
            return None
        candidate = re.sub(r"\b\d{4}-\d{2}-\d{2}.*$", "", candidate).strip()
        candidate = re.sub(r"\b\d+\s*[-:]\s*\d+\b", "", candidate).strip()
        cleaned = clean_text(candidate)
        return cleaned
