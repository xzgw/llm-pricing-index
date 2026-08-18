"""Unit tests for deepseek_scraper, against a saved (real, trimmed) HTML
fixture — no network calls. The fixture is the single pricing `<table>`
verbatim from https://api-docs.deepseek.com/quick_start/pricing/ (fetched
2026-08-18), so the expected values below are the vendor's real published
numbers, not invented ones.
"""

from pathlib import Path
from unittest import mock

import pytest

from scrapers import deepseek_scraper

FIXTURE = (Path(__file__).parent / "fixtures" / "deepseek_pricing.html").read_text()


def test_scrapes_both_models_with_off_peak_cache_miss_price_as_canonical():
    with mock.patch("scrapers.deepseek_scraper.fetch", return_value=FIXTURE):
        entries = deepseek_scraper.scrape("2026-08-18")

    by_model = {e["model"]: e for e in entries}
    assert set(by_model) == {"deepseek-v4-flash", "deepseek-v4-pro"}

    flash = by_model["deepseek-v4-flash"]
    assert flash["vendor"] == "deepseek"
    assert flash["input_price_usd"] == 0.22  # off-peak cache-miss, not the 0.44 peak rate
    assert flash["output_price_usd"] == 0.66  # off-peak output, not the 1.32 peak rate
    assert flash["cache_read_price_usd"] == 0.007  # off-peak cache-hit
    assert flash["cache_write_price_usd"] is None  # DeepSeek publishes no cache-write rate
    assert flash["last_verified"] == "2026-08-18"

    pro = by_model["deepseek-v4-pro"]
    assert pro["input_price_usd"] == 0.66
    assert pro["output_price_usd"] == 1.98
    assert pro["cache_read_price_usd"] == 0.022


def test_raises_when_page_structure_does_not_match():
    with mock.patch("scrapers.deepseek_scraper.fetch", return_value="<html><body>nothing here</body></html>"):
        with pytest.raises(RuntimeError):
            deepseek_scraper.scrape("2026-08-18")
