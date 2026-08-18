"""Unit tests for alibaba_scraper, against a saved (real, trimmed) HTML
fixture — no network calls. `alibaba_pricing.html` keeps the real h2/h3/
table markup from https://www.alibabacloud.com/help/en/model-studio/
model-pricing (fetched 2026-08-18) for a handful of representative model
families (a single-row family, a rowspan-tiered multi-deployment-scope
family, a two-row-subheader family, and — past the section's stop
boundary — a third-party-resold family that must NOT be scraped), with
each table's very long tail of dated-snapshot rows truncated. Every price
asserted below is the vendor's real published number for that row, not an
invented one.
"""

from pathlib import Path
from unittest import mock

import pytest

from scrapers import alibaba_scraper

FIXTURE = (Path(__file__).parent / "fixtures" / "alibaba_pricing.html").read_text()


def _scrape():
    with mock.patch("scrapers.alibaba_scraper.fetch", return_value=FIXTURE):
        return alibaba_scraper.scrape("2026-08-18")


def test_scrapes_simple_single_row_family():
    by_model = {e["model"]: e for e in _scrape()}
    turbo = by_model["qwen-turbo"]
    assert turbo["vendor"] == "alibaba"
    assert turbo["input_price_usd"] == 0.05
    assert turbo["output_price_usd"] == 0.2
    # Alibaba folds caching into the headline price rather than publishing
    # a separate rate in this table — never fabricated as 0 or guessed.
    assert turbo["cache_read_price_usd"] is None
    assert turbo["cache_write_price_usd"] is None


def test_skips_rowspan_tiered_continuation_rows_keeping_only_the_first_bracket():
    by_model = {e["model"]: e for e in _scrape()}
    # qwen3-max's first bracket (0<Token<=32K) is $1.2/$6; its rowspanned
    # continuation brackets (32K<Token<=128K -> $2.4/$12, 128K<Token<=256K
    # -> $3/$15) must NOT overwrite or duplicate this.
    assert by_model["qwen3-max"]["input_price_usd"] == 1.2
    assert by_model["qwen3-max"]["output_price_usd"] == 6.0


def test_filters_to_international_deployment_scope_only():
    entries = _scrape()
    # qwen-max appears in the fixture under both International ($1.6/$6.4)
    # and other deployment scopes with different prices — exactly one
    # entry should survive, at the International rate.
    max_entries = [e for e in entries if e["model"] == "qwen-max"]
    assert len(max_entries) == 1
    assert max_entries[0]["input_price_usd"] == 1.6
    assert max_entries[0]["output_price_usd"] == 6.4


def test_two_row_subheader_table_takes_first_non_thinking_sub_column():
    by_model = {e["model"]: e for e in _scrape()}
    # qwen-plus's Output price splits into Non-Thinking ($1.2) / Thinking
    # ($4) sub-columns via a second header row; Non-Thinking (first-listed,
    # cheaper/base mode) is canonical.
    plus = by_model["qwen-plus"]
    assert plus["input_price_usd"] == 0.4
    assert plus["output_price_usd"] == 1.2


def test_does_not_scrape_past_the_third_party_models_boundary():
    by_model = {e["model"]: e for e in _scrape()}
    # The fixture includes a real "DeepSeek" table (Alibaba's resale of
    # deepseek-v4-pro/-flash) positioned after the
    # "Text generation - third-party models" stop heading. Those rows carry
    # Alibaba's own resale prices (e.g. deepseek-v4-pro at $2.4/$4.8), which
    # must never be attributed to vendor "alibaba" here — deepseek_scraper
    # is the authoritative source for DeepSeek's own pricing.
    assert "deepseek-v4-pro" not in by_model
    assert "deepseek-v4-flash" not in by_model


def test_raises_when_target_sections_are_missing():
    with mock.patch("scrapers.alibaba_scraper.fetch", return_value="<html><body>nothing here</body></html>"):
        with pytest.raises(RuntimeError):
            alibaba_scraper.scrape("2026-08-18")
