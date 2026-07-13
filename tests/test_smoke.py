"""Smoke tests for packaging and basic module imports."""

from pathlib import Path

from dota2_scraper import __version__
from dota2_scraper.config import Settings, get_settings


def test_version():
    assert __version__


def test_settings_defaults():
    settings = Settings()
    assert settings.db_path.name == "dota2.db"
    assert "dotabuff" in str(settings.dotabuff_base_url)


def test_schema_file_ships_with_package():
    settings = get_settings()
    assert settings.schema_path.exists(), f"Missing schema at {settings.schema_path}"
    text = settings.schema_path.read_text(encoding="utf-8-sig")
    assert "CREATE TABLE" in text.upper()
