from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dota2_scraper.models import Source
from dota2_scraper.utils import detect_region, json_dumps, stable_id


class OpenDotaParser:
    """Maps OpenDota API payloads into existing database row shapes."""

    def __init__(
        self,
        hero_names_by_id: dict[int, str],
        pro_players_by_account_id: dict[int, dict[str, Any]] | None = None,
        leagues_by_id: dict[int, dict[str, Any]] | None = None,
    ) -> None:
        self.hero_names_by_id = hero_names_by_id
        self.pro_players_by_account_id = pro_players_by_account_id or {}
        self.leagues_by_id = leagues_by_id or {}

    def parse_team_payload(self, team_payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        rows: dict[str, list[dict[str, Any]]] = {"teams": []}
        if not team_payload:
            return rows
        team_id = self._int(team_payload.get("team_id") or team_payload.get("id"))
        team_name = self._text(team_payload.get("name"))
        if team_id is None or not team_name:
            return rows
        rows["teams"].append(
            {
                "source": Source.OPENDOTA.value,
                "source_id": str(team_id),
                "name": team_name,
                "rating": self._float(team_payload.get("rating")),
                "wins": self._int(team_payload.get("wins")),
                "losses": self._int(team_payload.get("losses")),
                "raw_json": json_dumps(team_payload),
            }
        )
        return rows

    def parse_match_payload(
        self,
        pro_match: dict[str, Any],
        match_details: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        rows: dict[str, list[dict[str, Any]]] = {
            "teams": [],
            "players": [],
            "tournaments": [],
            "matches": [],
            "games": [],
            "player_game_stats": [],
            "drafts": [],
            "opendota_objectives": [],
        }

        match_id = self._int(match_details.get("match_id") or pro_match.get("match_id"))
        if match_id is None:
            return rows
        match_source_id = str(match_id)

        radiant_team = self._team_from_payload(pro_match.get("radiant_team"), "radiant")
        dire_team = self._team_from_payload(pro_match.get("dire_team"), "dire")
        if radiant_team.get("team_id") is None:
            radiant_team["team_id"] = self._int(match_details.get("radiant_team_id"))
        if dire_team.get("team_id") is None:
            dire_team["team_id"] = self._int(match_details.get("dire_team_id"))
        if not radiant_team.get("name"):
            radiant_team["name"] = self._text(match_details.get("radiant_name")) or self._text(pro_match.get("radiant_name"))
        if not dire_team.get("name"):
            dire_team["name"] = self._text(match_details.get("dire_name")) or self._text(pro_match.get("dire_name"))

        league_id = self._int(match_details.get("leagueid") or pro_match.get("leagueid"))
        league_name = self._league_name(league_id, pro_match.get("league"))
        if league_id is not None and league_name:
            rows["tournaments"].append(
                {
                    "source": Source.OPENDOTA.value,
                    "source_id": str(league_id),
                    "name": league_name,
                    "tier": self._int((self.leagues_by_id.get(league_id) or {}).get("tier")),
                    "region": self._text((self.leagues_by_id.get(league_id) or {}).get("region")) or detect_region(league_name),
                    "raw_json": json_dumps(self.leagues_by_id.get(league_id) or {}),
                }
            )

        for team in (radiant_team, dire_team):
            team_id = team.get("team_id")
            team_name = team.get("name")
            if team_id is None or not team_name:
                continue
            rows["teams"].append(
                {
                    "source": Source.OPENDOTA.value,
                    "source_id": str(team_id),
                    "name": team_name,
                    "region": detect_region(team_name),
                    "raw_json": json_dumps(team),
                }
            )

        start_time = self._int(match_details.get("start_time") or pro_match.get("start_time"))
        completed_at = self._utc_from_unix(start_time)
        radiant_win = match_details.get("radiant_win")
        winning_side = "radiant" if radiant_win is True else "dire" if radiant_win is False else None

        rows["matches"].append(
            {
                "source": Source.OPENDOTA.value,
                "source_id": match_source_id,
                "tournament_name": league_name,
                "team_a_name": radiant_team.get("name"),
                "team_b_name": dire_team.get("name"),
                "scheduled_at_utc": completed_at,
                "completed_at_utc": completed_at,
                "status": "completed",
                "raw_json": json_dumps(
                    {
                        "pro_match": pro_match,
                        "league_id": league_id,
                    }
                ),
            }
        )

        duration = self._int(match_details.get("duration"))
        patch = self._int(match_details.get("patch"))
        rows["games"].append(
            {
                "source": Source.OPENDOTA.value,
                "source_id": match_source_id,
                "game_number": 1,
                "radiant_team_name": radiant_team.get("name"),
                "dire_team_name": dire_team.get("name"),
                "winning_side": winning_side,
                "duration_seconds": duration,
                "opendota_match_id": match_id,
                "radiant_gold_adv_json": json_dumps(match_details.get("radiant_gold_adv")),
                "radiant_xp_adv_json": json_dumps(match_details.get("radiant_xp_adv")),
                "patch": patch,
                "raw_json": json_dumps({"match_id": match_id}),
            }
        )

        first_pick_team_name = None
        for pick_ban in match_details.get("picks_bans") or []:
            order = self._int(pick_ban.get("order"))
            if order is None:
                continue
            hero_id = self._int(pick_ban.get("hero_id"))
            team_flag = self._int(pick_ban.get("team"))
            is_pick = bool(pick_ban.get("is_pick"))
            team_name = radiant_team.get("name") if team_flag == 0 else dire_team.get("name") if team_flag == 1 else None
            if first_pick_team_name is None and is_pick:
                first_pick_team_name = team_name

            rows["drafts"].append(
                {
                    "source": Source.OPENDOTA.value,
                    "source_id": stable_id("opendota-draft", match_source_id, order, hero_id, is_pick),
                    "_game_source_id": match_source_id,
                    "first_pick_team_name": first_pick_team_name,
                    "pick_ban_order": order,
                    "hero_id": hero_id,
                    "raw_json": json_dumps(
                        {
                            "team": team_flag,
                            "is_pick": is_pick,
                            "hero_name": self.hero_name(hero_id),
                        }
                    ),
                }
            )

        for player in match_details.get("players") or []:
            player_slot = self._int(player.get("player_slot"))
            is_radiant = player_slot is not None and player_slot < 128
            team_name = radiant_team.get("name") if is_radiant else dire_team.get("name")

            account_id = self._int(player.get("account_id"))
            player_meta = self.pro_players_by_account_id.get(account_id or -1, {})
            ign = (
                self._text(player_meta.get("name"))
                or self._text(player.get("name"))
                or self._text(player.get("personaname"))
                or (str(account_id) if account_id is not None else None)
            )
            if ign:
                rows["players"].append(
                    {
                        "source": Source.OPENDOTA.value,
                        "source_id": str(account_id) if account_id is not None else stable_id("anon-player", match_source_id, player_slot),
                        "ign": ign,
                        "nationality": self._text(player_meta.get("country_code")),
                        "raw_json": json_dumps(player_meta),
                    }
                )

            hero_id = self._int(player.get("hero_id"))
            obs_placed = self._int(player.get("obs_placed"))
            sen_placed = self._int(player.get("sen_placed"))
            teamfight_participation = self._float(player.get("teamfight_participation"))
            player_source_id = stable_id("opendota-player", match_source_id, account_id, player_slot)

            rows["player_game_stats"].append(
                {
                    "source": Source.OPENDOTA.value,
                    "source_id": player_source_id,
                    "_game_source_id": match_source_id,
                    "player_ign": ign,
                    "team_name": team_name,
                    "hero_name": self.hero_name(hero_id),
                    "kills": self._int(player.get("kills")),
                    "deaths": self._int(player.get("deaths")),
                    "assists": self._int(player.get("assists")),
                    "gpm": self._int(player.get("gold_per_min")),
                    "xpm": self._int(player.get("xp_per_min")),
                    "last_hits": self._int(player.get("last_hits")),
                    "denies": self._int(player.get("denies")),
                    "hero_damage": self._int(player.get("hero_damage")),
                    "tower_damage": self._int(player.get("tower_damage")),
                    "hero_healing": self._int(player.get("hero_healing")),
                    "final_items_json": json_dumps([
                        self._int(player.get("item_0")),
                        self._int(player.get("item_1")),
                        self._int(player.get("item_2")),
                        self._int(player.get("item_3")),
                        self._int(player.get("item_4")),
                        self._int(player.get("item_5")),
                    ]),
                    "backpack_items_json": json_dumps([
                        self._int(player.get("backpack_0")),
                        self._int(player.get("backpack_1")),
                        self._int(player.get("backpack_2")),
                    ]),
                    "neutral_item": self._text(player.get("item_neutral")),
                    "item_purchase_times_json": json_dumps(player.get("purchase")),
                    "obs_placed": obs_placed,
                    "sen_placed": sen_placed,
                    "observer_wards_placed": obs_placed,
                    "sentry_wards_placed": sen_placed,
                    "camps_stacked": self._int(player.get("camps_stacked")),
                    "teamfight_participation": teamfight_participation,
                    "teamfight_participation_pct": teamfight_participation,
                    "gold_t_json": json_dumps(player.get("gold_t")),
                    "xp_t_json": json_dumps(player.get("xp_t")),
                    "lh_t_json": json_dumps(player.get("lh_t")),
                    "raw_json": json_dumps(player),
                }
            )

        for obj in match_details.get("objectives") or []:
            obj_type = self._text(obj.get("type"))
            if not obj_type:
                continue
            rows["opendota_objectives"].append(
                {
                    "_game_source_id": match_source_id,
                    "time": self._int(obj.get("time")),
                    "type": obj_type,
                    "team": self._int(obj.get("team")),
                    "key": self._objective_key(obj),
                    "slot": self._int(obj.get("slot") or obj.get("player_slot")),
                }
            )

        return rows

    def hero_name(self, hero_id: int | None) -> str | None:
        if hero_id is None:
            return None
        return self.hero_names_by_id.get(hero_id)

    def _league_name(self, league_id: int | None, league_field: Any) -> str | None:
        league_name = self._text(league_field)
        if league_name:
            return league_name
        if league_id is None:
            return None
        return self._text((self.leagues_by_id.get(league_id) or {}).get("name"))

    def _team_from_payload(self, payload: Any, side: str) -> dict[str, Any]:
        if isinstance(payload, dict):
            team_id = self._int(payload.get("team_id") or payload.get("id"))
            name = self._text(payload.get("name"))
            return {"team_id": team_id, "name": name, "side": side}
        if isinstance(payload, int):
            return {"team_id": payload, "name": None, "side": side}
        return {"team_id": None, "name": None, "side": side}

    def _objective_key(self, objective: dict[str, Any]) -> str | None:
        key = objective.get("key")
        text_key = self._text(key)
        if text_key and text_key.isdigit():
            maybe_hero = self.hero_name(int(text_key))
            if maybe_hero:
                return maybe_hero
        if isinstance(key, int):
            hero_name = self.hero_name(key)
            if hero_name:
                return hero_name
        return text_key

    @staticmethod
    def _utc_from_unix(value: int | None) -> str | None:
        if value is None:
            return None
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat()

    @staticmethod
    def _text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _int(value: Any) -> int | None:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
