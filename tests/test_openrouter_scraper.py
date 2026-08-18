"""OpenRouter scraper: gap-filling behaviour and the aggregator marker.

These are the two properties that matter and that a future refactor could
silently break — a duplicated model would make a consumer's batch import
create two price rules for one model, and a missing source_kind would let a
reseller's marked-up price be read as the vendor's official rate.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from scrapers import openrouter_scraper

API_PAYLOAD = json.dumps({
    "data": [
        # Already covered by an official scraper → must be skipped.
        {"id": "qwen/qwen3-max", "pricing": {"prompt": "0.0000012", "completion": "0.000006"}},
        # Not covered → must be emitted.
        {"id": "bytedance-seed/seed-2.0-mini", "pricing": {"prompt": "0.0000001", "completion": "0.0000004"}},
        # Priced per-image (no per-token figure) → skipped, NOT emitted as $0.
        {"id": "someone/image-model", "pricing": {"prompt": "-1", "completion": "-1"}},
    ]
})


def _scrape_with(covered):
    with patch.object(openrouter_scraper, "fetch", return_value=API_PAYLOAD), \
         patch.object(openrouter_scraper, "_already_covered", return_value=covered):
        return openrouter_scraper.scrape("2026-08-18")


def test_skips_models_an_official_scraper_already_covers():
    got = {e["model"] for e in _scrape_with({"qwen3-max"})}
    assert "qwen3-max" not in got, (
        "a model already priced by its own vendor must not also be emitted here — "
        "two entries for one model make the consumer create duplicate price rules"
    )
    assert "seed-2.0-mini" in got


def test_every_entry_is_marked_as_an_aggregator_price():
    for e in _scrape_with(set()):
        assert e["source_kind"] == "aggregator", (
            "OpenRouter's number is its own selling price, not the vendor's official "
            "rate; without the marker a consumer using this file as a price ceiling "
            "cannot tell the two apart"
        )
        assert e["vendor"] == "openrouter"


def test_per_token_prices_are_converted_to_per_million():
    entry = next(e for e in _scrape_with(set()) if e["model"] == "seed-2.0-mini")
    # 0.0000001 USD/token = 0.1 USD per 1M tokens
    assert entry["input_price_usd"] == 0.1
    assert entry["output_price_usd"] == 0.4


def test_models_with_no_per_token_price_are_skipped_not_zeroed():
    got = {e["model"] for e in _scrape_with(set())}
    assert "image-model" not in got, (
        "a per-image model has no per-token rate; emitting 0 would read as 'free' "
        "rather than 'not expressible in this schema'"
    )


def test_empty_result_raises_rather_than_wiping_the_block():
    payload = json.dumps({"data": [{"id": "a/b", "pricing": {"prompt": "-1", "completion": "-1"}}]})
    with patch.object(openrouter_scraper, "fetch", return_value=payload), \
         patch.object(openrouter_scraper, "_already_covered", return_value=set()):
        try:
            openrouter_scraper.scrape("2026-08-18")
        except ValueError:
            return
    raise AssertionError("an empty result must raise so run.py keeps last-known-good, not silently blank the vendor block")
