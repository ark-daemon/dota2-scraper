from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Source(StrEnum):
    DOTABUFF = "dotabuff"
    LIQUIPEDIA = "liquipedia"
    OPENDOTA = "opendota"
    DLTV = "dltv"


class PageKind(StrEnum):
    DOTABUFF_ESPORTS = "dotabuff_esports"
    DOTABUFF_MATCH = "dotabuff_match"
    DOTABUFF_TEAM = "dotabuff_team"
    LIQUIPEDIA_PORTAL = "liquipedia_portal"
    LIQUIPEDIA_TOURNAMENT = "liquipedia_tournament"
    LIQUIPEDIA_TEAM = "liquipedia_team"
    LIQUIPEDIA_PLAYER = "liquipedia_player"
    LIQUIPEDIA_MATCH = "liquipedia_match"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class FetchJob:
    url: str
    source: Source
    kind: PageKind = PageKind.UNKNOWN
    depth: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FetchedPage:
    job: FetchJob
    html: str
    final_url: str
    status_code: int | None = None


@dataclass(slots=True)
class ParsedPayload:
    source: Source
    url: str
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    discovered_jobs: list[FetchJob] = field(default_factory=list)

    def add(self, table: str, row: dict[str, Any]) -> None:
        self.rows.setdefault(table, []).append(row)
