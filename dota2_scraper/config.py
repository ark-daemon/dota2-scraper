from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DOTA2_",
        extra="ignore",
    )

    db_path: Path = Path("dota2.db")
    export_dir: Path = Path("exports")
    log_dir: Path = Path("logs")

    dotabuff_base_url: HttpUrl = "https://www.dotabuff.com"
    liquipedia_base_url: HttpUrl = "https://liquipedia.net"
    opendota_base_url: HttpUrl = "https://api.opendota.com/api"
    dltv_base_url: HttpUrl = "https://dltv.org"
    browser_fingerprint_seed: int = 42069

    dotabuff_concurrency: int = Field(default=2, ge=1, le=5)
    liquipedia_concurrency: int = Field(default=4, ge=1, le=8)
    dltv_concurrency: int = Field(default=1, ge=1, le=3)
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    dotabuff_delay_seconds: float = Field(default=2.5, ge=0)
    liquipedia_delay_seconds: float = Field(default=1.5, ge=0)
    dltv_delay_seconds: float = Field(default=0.5, ge=0)
    max_pages_per_run: int = Field(default=100, ge=1)

    user_agent: str = "Dota2EsportsResearchBot/0.1 (contact: replace-with-email@example.com)"

    dotabuff_seed_urls: tuple[str, ...] = ("https://www.dotabuff.com/esports",)
    liquipedia_seed_urls: tuple[str, ...] = (
        "https://liquipedia.net/dota2/Portal:Tournaments",
        "https://liquipedia.net/dota2/Portal:Teams",
        "https://liquipedia.net/dota2/Liquipedia:Upcoming_and_ongoing_matches",
    )

    @property
    def schema_path(self) -> Path:
        return Path(__file__).parent / "schemas" / "schema.sql"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
