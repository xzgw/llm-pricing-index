"""Unit tests for minimax_scraper, against a saved (real, trimmed) markdown
fixture — no network calls. The fixture is the "## LLM" ... "## Audio"
slice, verbatim, of https://platform.minimax.io/docs/guides/pricing-paygo.md
(fetched 2026-08-18) — the expected values below are the vendor's real
published numbers, not invented ones.
"""

from pathlib import Path
from unittest import mock

import pytest

from scrapers import minimax_scraper

FIXTURE = (Path(__file__).parent / "fixtures" / "minimax_pricing.md").read_text()


def test_takes_standard_tab_and_permanent_discounted_price_not_priority_or_list_price():
    with mock.patch("scrapers.minimax_scraper.fetch", return_value=FIXTURE):
        entries = minimax_scraper.scrape("2026-08-18")

    by_model = {e["model"]: e for e in entries}
    m3 = by_model["minimax-m3"]
    assert m3["vendor"] == "minimax"
    # Cell is "~~$0.60~~ $0.30 / M tokens" (Standard tab) — the struck-through
    # list price is 0.60, the Priority-tab price is 0.45/0.90; neither should
    # win over the actual current Standard price.
    assert m3["input_price_usd"] == 0.3
    assert m3["output_price_usd"] == 1.2
    assert m3["cache_read_price_usd"] == 0.06
    assert m3["cache_write_price_usd"] is None  # M3's table has no write column


def test_tiered_row_keeps_first_seen_lower_tier_and_skips_the_512k_plus_row():
    with mock.patch("scrapers.minimax_scraper.fetch", return_value=FIXTURE):
        entries = minimax_scraper.scrape("2026-08-18")

    minimax_models = [e for e in entries if e["model"] == "minimax-m3"]
    assert len(minimax_models) == 1  # not two, despite two MiniMax-M3 rows in the source


def test_plain_and_legacy_tables_include_cache_write_column():
    with mock.patch("scrapers.minimax_scraper.fetch", return_value=FIXTURE):
        entries = minimax_scraper.scrape("2026-08-18")
    by_model = {e["model"]: e for e in entries}

    m27 = by_model["minimax-m2.7"]
    assert m27["input_price_usd"] == 0.3
    assert m27["output_price_usd"] == 1.2
    assert m27["cache_write_price_usd"] == 0.375

    legacy = by_model["minimax-m2"]
    assert legacy["input_price_usd"] == 0.3
    assert legacy["cache_write_price_usd"] == 0.375


def test_raises_when_llm_section_is_missing():
    with mock.patch("scrapers.minimax_scraper.fetch", return_value="# Product Pricing\n\nnothing relevant here\n"):
        with pytest.raises(RuntimeError):
            minimax_scraper.scrape("2026-08-18")
