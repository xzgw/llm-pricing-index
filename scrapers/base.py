"""Shared helpers for per-vendor scrapers.

Each vendor scraper module exposes one function:

    def scrape() -> list[dict]:
        ...returns a list of price entries (see schema/prices.schema.json)

Scrapers must not raise for "page changed slightly" conditions they can
recover from — but if they genuinely can't produce data, raising is correct:
run.py isolates each vendor in its own try/except so one vendor's failure
never blocks the others (see docs in run.py).
"""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass, field


USER_AGENT = "llm-pricing-index-bot/1.0 (+https://github.com/xzgw/llm-pricing-index)"


def fetch(url: str, timeout: int = 30) -> str:
    """Fetch a URL as text. Raises on any HTTP/network error — callers
    (per-vendor scrapers) should let this propagate; run.py's isolation
    layer is what prevents one vendor's fetch failure from affecting others.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_usd(text: str) -> float | None:
    """Parse a price cell like "$0.60", "0.60", "Free", "—" into a float.
    Returns None for non-numeric cells ("—", "N/A") — caller decides whether
    that means "omit this field" or "skip this row". Returns 0.0 for "Free"
    (a genuine zero price, not a missing value).
    """
    t = text.strip()
    if not t or t in ("—", "-", "N/A", "n/a"):
        return None
    if t.lower() == "free":
        return 0.0
    m = re.search(r"[\d.]+", t.replace(",", ""))
    if not m:
        return None
    return float(m.group(0))


@dataclass
class PriceEntry:
    model: str
    vendor: str
    input_price_usd: float
    output_price_usd: float
    source_url: str
    last_verified: str
    cache_read_price_usd: float | None = None
    cache_write_price_usd: float | None = None

    def to_dict(self) -> dict:
        d = {
            "model": self.model,
            "vendor": self.vendor,
            "input_price_usd": self.input_price_usd,
            "output_price_usd": self.output_price_usd,
            "cache_read_price_usd": self.cache_read_price_usd,
            "cache_write_price_usd": self.cache_write_price_usd,
            "source_url": self.source_url,
            "last_verified": self.last_verified,
        }
        return d
