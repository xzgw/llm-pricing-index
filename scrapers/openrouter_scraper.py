"""OpenRouter — gap-filling secondary source.

WHY THIS EXISTS. Every other scraper here reads one vendor's own pricing page.
That leaves holes: a vendor whose page is JS-rendered (ByteDance/Volcengine),
or a model the vendor lists only in a locale we don't scrape. OpenRouter
publishes a single machine-readable JSON API covering 400+ models in USD, so
one request closes several of those holes at once and keeps up with new models
without a new scraper each time.

WHAT IT IS NOT. OpenRouter is a reseller. Its number is OPENROUTER'S SELLING
PRICE, not the vendor's official rate, and it is typically higher. Entries from
here therefore carry source_kind="aggregator" so a consumer using this file as a
price ceiling can tell the two apart:

    vendor_official  → "what does this model actually list for"
    aggregator       → "what would my customer pay if they went elsewhere"

Both are legitimate ceilings; they are not the same number and must not be
averaged, preferred by recency, or silently substituted for one another.

GAP-FILLING, NOT COMPETING. This scraper emits an entry ONLY for models no
official-vendor scraper already covers. Two rows for one model would make a
consumer's batch import produce duplicate price rules. Because run.py replaces
this vendor's whole block on every run, the gap set is recomputed each time: if
an official scraper later gains a model, the openrouter row for it disappears
on the next run without any special handling.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import fetch

API_URL = "https://openrouter.ai/api/v1/models"

# The public page a human should check to verify one of these numbers. The API
# is the machine source; this is where the same figure is shown to a person.
SOURCE_URL = "https://openrouter.ai/models"

PRICES_FILE = Path(__file__).resolve().parent.parent / "prices.json"


def _already_covered() -> set[str]:
    """Model names (lowercased) an official-vendor scraper already provides.

    Entries previously written by THIS scraper are excluded, so a model does
    not count as covered merely because we filled it in last run — otherwise
    the gap set could never shrink when an official source catches up.
    """
    if not PRICES_FILE.exists():
        return set()
    try:
        entries = json.loads(PRICES_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    return {
        e["model"].lower()
        for e in entries
        if e.get("vendor") != "openrouter" and e.get("model")
    }


def _usd_per_million(raw: str | float | None) -> float | None:
    """OpenRouter quotes USD per single token; this file is USD per 1M."""
    if raw in (None, "", "-1"):  # -1 marks "not applicable" in their API
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v < 0:
        return None
    return round(v * 1_000_000, 6)


def scrape(last_verified: str) -> list[dict]:
    payload = json.loads(fetch(API_URL))
    models = payload.get("data")
    if not models:
        raise ValueError("openrouter: /api/v1/models returned no data array")

    covered = _already_covered()
    out: list[dict] = []
    for m in models:
        model_id = m.get("id") or ""
        # "qwen/qwen3-max" → "qwen3-max": match how vendor scrapers name models,
        # and how a gateway's model list names them.
        name = model_id.split("/")[-1]
        if not name or name.lower() in covered:
            continue

        pricing = m.get("pricing") or {}
        inp = _usd_per_million(pricing.get("prompt"))
        outp = _usd_per_million(pricing.get("completion"))
        # Both are required by the schema. A model priced per-image or per-second
        # has no per-token figure here; skip rather than invent a zero — a zero
        # would read as "free" instead of "not expressible in this schema".
        if inp is None or outp is None:
            continue

        entry = {
            "model": name,
            "vendor": "openrouter",
            "input_price_usd": inp,
            "output_price_usd": outp,
            "source_url": SOURCE_URL,
            "last_verified": last_verified,
            "source_kind": "aggregator",
        }
        cache_read = _usd_per_million(pricing.get("input_cache_read"))
        if cache_read is not None:
            entry["cache_read_price_usd"] = cache_read
        cache_write = _usd_per_million(pricing.get("input_cache_write"))
        if cache_write is not None:
            entry["cache_write_price_usd"] = cache_write
        out.append(entry)

    if not out:
        raise ValueError("openrouter: every model was already covered or unpriced — refusing to report an empty block")
    return out
