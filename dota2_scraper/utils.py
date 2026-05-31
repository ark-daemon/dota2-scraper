from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def parse_int(value: Any) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"-?\d[\d,]*", text)
    if not match:
        return None
    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_float(value: Any) -> float | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_pct(value: Any) -> float | None:
    number = parse_float(value)
    if number is None:
        return None
    return number / 100 if "%" in str(value) else number


def parse_money(value: Any) -> float | None:
    return parse_float(value)


def parse_duration_seconds(value: Any) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    parts = [int(p) for p in re.findall(r"\d+", text)]
    if not parts:
        return None
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


def json_dumps(value: Any) -> str | None:
    if value in (None, {}, []):
        return None
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def stable_id(*parts: Any) -> str:
    raw = "|".join(clean_text(part) or "" for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def now_utc_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


def absolute_url(base_url: str, href: str | None) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith("#") or href.startswith("javascript:"):
        return None
    return urljoin(base_url, href)




def side_from_text(value: Any) -> str | None:
    text = (clean_text(value) or "").lower()
    if "radiant" in text:
        return "Radiant"
    if "dire" in text:
        return "Dire"
    return None


def detect_region(value: Any) -> str | None:
    text = (clean_text(value) or "").upper()
    aliases = {
        "WESTERN EUROPE": "WEU",
        "WEU": "WEU",
        "EASTERN EUROPE": "EEU",
        "EEU": "EEU",
        "NORTH AMERICA": "NA",
        "NA": "NA",
        "SOUTH AMERICA": "SA",
        "SA": "SA",
        "CHINA": "CN",
        "CN": "CN",
        "SOUTHEAST ASIA": "SEA",
        "SEA": "SEA",
        "MENA": "MENA",
        "MIDDLE EAST": "MENA",
    }
    for key, region in aliases.items():
        if key in text:
            return region
    return None
