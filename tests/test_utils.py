"""Unit tests for shared parsing helpers."""

from dota2_scraper.utils import clean_text, parse_float, parse_int, parse_money


def test_parse_int_commas() -> None:
    assert parse_int("1,234") == 1234
    assert parse_int(None) is None


def test_parse_float_percent_noise() -> None:
    assert parse_float("12.5%") == 12.5


def test_parse_money() -> None:
    assert parse_money("$1,500,000") == 1500000.0


def test_clean_text() -> None:
    assert clean_text("  hello   world  ") == "hello world"
