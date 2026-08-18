"""Unit tests for moonshot_scraper, against saved (real, trimmed) fixtures —
no network calls. `kimi_pricing_overview.html` keeps only the real <a
href> navigation links from https://platform.kimi.ai/docs/pricing/chat;
the per-model `.md` fixtures are the vendor's real Mintlify markdown
source for each pricing sub-page (fetched 2026-08-18), unmodified — the
expected values below are Moonshot's real published numbers, not invented
ones.
"""

from pathlib import Path
from unittest import mock

import pytest

from scrapers import moonshot_scraper

FIXTURES = Path(__file__).parent / "fixtures"
OVERVIEW_HTML = (FIXTURES / "kimi_pricing_overview.html").read_text()

PAGE_FIXTURES = {
    "chat-k3": (FIXTURES / "kimi_chat-k3.md").read_text(),
    "chat-k26": (FIXTURES / "kimi_chat-k26.md").read_text(),
    "chat-k27-code": (FIXTURES / "kimi_chat-k27-code.md").read_text(),
    "chat-k25": (FIXTURES / "kimi_chat-k25.md").read_text(),
    "chat-v1": (FIXTURES / "kimi_chat-v1.md").read_text(),
}


def _fake_fetch(url: str) -> str:
    if url == moonshot_scraper.SOURCE_URL:
        return OVERVIEW_HTML
    for slug, content in PAGE_FIXTURES.items():
        if url.endswith(f"{slug}.md"):
            return content
    raise AssertionError(f"unexpected URL fetched: {url}")


def test_discovers_and_scrapes_every_linked_pricing_page():
    with mock.patch("scrapers.moonshot_scraper.fetch", side_effect=_fake_fetch):
        entries = moonshot_scraper.scrape("2026-08-18")

    by_model = {e["model"]: e for e in entries}
    # One current flagship model, one legacy Moonshot V1 tier, and the
    # not-featured-but-still-live K2.5 (discovered via the nav links, not
    # the overview's curated card list) should all be present.
    assert "kimi-k3" in by_model
    assert "kimi-k2.5" in by_model
    assert "moonshot-v1-8k" in by_model


def test_cache_hit_and_cache_miss_columns_map_to_the_right_schema_fields():
    with mock.patch("scrapers.moonshot_scraper.fetch", side_effect=_fake_fetch):
        entries = moonshot_scraper.scrape("2026-08-18")
    by_model = {e["model"]: e for e in entries}

    k3 = by_model["kimi-k3"]
    assert k3["vendor"] == "moonshot"
    assert k3["input_price_usd"] == 3.0  # "Input Price (Cache Miss)" -> input_price_usd
    assert k3["output_price_usd"] == 15.0
    assert k3["cache_read_price_usd"] == 0.3  # "Input Price (Cache Hit)" -> cache_read_price_usd
    assert k3["cache_write_price_usd"] is None  # no vendor page publishes a write rate


def test_moonshot_v1_page_has_no_cache_columns_at_all():
    with mock.patch("scrapers.moonshot_scraper.fetch", side_effect=_fake_fetch):
        entries = moonshot_scraper.scrape("2026-08-18")
    by_model = {e["model"]: e for e in entries}

    v1 = by_model["moonshot-v1-8k"]
    assert v1["input_price_usd"] == 0.2  # bare "Input Price" column (no cache split)
    assert v1["output_price_usd"] == 2.0
    assert v1["cache_read_price_usd"] is None


def test_raises_when_overview_page_has_no_pricing_links():
    with mock.patch("scrapers.moonshot_scraper.fetch", return_value="<html><body>nothing here</body></html>"):
        with pytest.raises(RuntimeError):
            moonshot_scraper.scrape("2026-08-18")
