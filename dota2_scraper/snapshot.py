"""Fleet match-level snapshot export for dota2-scraper."""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

REPO_SLUG = "dota"
GAME = "Dota 2"
SCHEMA_VERSION = "1.0"

COLUMNS = [
    "match_id",
    "match_date",
    "team_a",
    "team_b",
    "winner",
    "source_url",
    "status",
    "score_a",
    "score_b",
    "event_name",
    "format",
    "raw_status",
]

STATUS_MAP = {
    "completed": "completed",
    "complete": "completed",
    "finished": "completed",
    "live": "live",
    "ongoing": "live",
    "upcoming": "scheduled",
    "scheduled": "scheduled",
    "canceled": "canceled",
    "cancelled": "canceled",
    "postponed": "postponed",
}

_stats = {
    "status_mapped": 0,
    "status_heuristic": 0,
    "date_status_anomaly": 0,
    "dropped_no_teams": 0,
    "dropped_missing_source_id": 0,
    "rows_out": 0,
}


def _reset_stats() -> None:
    for k in _stats:
        _stats[k] = 0


def _normalize_status(raw: str | None, score_a: int | None, score_b: int | None) -> str:
    key = (raw or "").strip().lower()
    if key in STATUS_MAP:
        _stats["status_mapped"] += 1
        return STATUS_MAP[key]
    _stats["status_heuristic"] += 1
    if score_a is not None or score_b is not None:
        return "completed"
    return "scheduled"


def _parse_date(
    completed: str | None,
    scheduled: str | None,
    *,
    status: str,
    has_scores: bool,
) -> str | None:
    next_year = datetime.now(UTC).year + 1

    def _one(s: str | None) -> datetime | None:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            return None

    parsed = _one(completed) or _one(scheduled)
    used_completed = _one(completed) is not None
    if parsed is None:
        return None
    if parsed.year < 2015 or parsed.year > next_year:
        logger.warning("Dota date out of bounds: {}", parsed.isoformat())
        return None
    if used_completed and status == "scheduled" and not has_scores:
        _stats["date_status_anomaly"] += 1
        logger.warning("Dota date/status anomaly")
    return parsed.date().isoformat()


def build_rows(db_path: str | Path) -> list[dict[str, Any]]:
    _reset_stats()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT id, source, source_id, tournament_name, team_a_name, team_b_name,
               team_a_score, team_b_score, series_format, scheduled_at_utc, completed_at_utc, status
        FROM matches
        """
    )
    rows_out: list[dict[str, Any]] = []
    for r in cur:
        team_a = (r["team_a_name"] or "").strip() or None
        team_b = (r["team_b_name"] or "").strip() or None
        if not team_a and not team_b:
            _stats["dropped_no_teams"] += 1
            continue
        source = (r["source"] or "").strip()
        source_id = (r["source_id"] or "").strip() if r["source_id"] is not None else ""
        if not source or not source_id:
            _stats["dropped_missing_source_id"] += 1
            logger.warning("Dota row id={} missing source/source_id; dropped", r["id"])
            continue

        score_a = int(r["team_a_score"]) if r["team_a_score"] is not None else None
        score_b = int(r["team_b_score"]) if r["team_b_score"] is not None else None
        raw_status = r["status"]
        status = _normalize_status(raw_status, score_a, score_b)
        has_scores = score_a is not None or score_b is not None
        match_date = _parse_date(
            r["completed_at_utc"], r["scheduled_at_utc"], status=status, has_scores=has_scores
        )

        winner = None
        if score_a is not None and score_b is not None and team_a and team_b:
            if score_a > score_b:
                winner = team_a
            elif score_b > score_a:
                winner = team_b

        rows_out.append(
            {
                "match_id": f"{REPO_SLUG}:{source}:{source_id}",
                "match_date": match_date,
                "team_a": team_a,
                "team_b": team_b,
                "winner": winner,
                "source_url": None,
                "status": status,
                "score_a": score_a,
                "score_b": score_b,
                "event_name": r["tournament_name"],
                "format": r["series_format"],
                "raw_status": raw_status,
            }
        )
    conn.close()
    _stats["rows_out"] = len(rows_out)
    return rows_out


def write_snapshot(db_path: str | Path, out_dir: str | Path) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = build_rows(db_path)
    if not rows:
        logger.warning("snapshot empty after filters")

    (out / "data.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "data.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k) for k in COLUMNS})
    try:
        import pandas as pd

        pd.DataFrame(rows, columns=COLUMNS).to_parquet(out / "data.parquet", index=False)
    except Exception as exc:
        logger.error("parquet failed: {}", exc)

    manifest = {
        "source": REPO_SLUG,
        "game": GAME,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "record_count": len(rows),
        "schema_version": SCHEMA_VERSION,
        "columns": COLUMNS,
        "files": {"json": "data.json", "csv": "data.csv", "parquet": "data.parquet"},
        "stats": dict(_stats),
        "id_strategy": "dota:{source}:{source_id}",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
