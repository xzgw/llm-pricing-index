"""z.ai (GLM) pricing scraper.

Source: https://docs.z.ai/guides/overview/pricing (Mintlify-hosted static docs
site — server-rendered HTML tables, no JS execution needed to read them).

Verified table structure (2026-07-03): two tables share the identical header
`['Model', 'Input', 'Cached Input', 'Cached Input Storage', 'Output']` — one
for text models, one for vision models. We match on that header signature
(not table position/index) so a reordering of unrelated tables on the page
doesn't break this. "Cached Input Storage" (a context-cache TTL/free-tier
column) has no corresponding field in our schema and is intentionally
ignored. z.ai does not publish a distinct cache-write rate; per this repo's
established convention (routed's own consumption of this data mirrors it),
cache_write_price_usd = input_price_usd whenever the model has a numeric
cache-read price at all.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import PriceEntry, fetch, parse_usd

SOURCE_URL = "https://docs.z.ai/guides/overview/pricing"
VENDOR = "z.ai"
EXPECTED_HEADER = ["Model", "Input", "Cached Input", "Cached Input Storage", "Output"]


def _model_id(display_name: str) -> str:
    """"GLM-4.5-Air" -> "glm-4.5-air" (matches routed's existing naming)."""
    return display_name.strip().lower()


def scrape(last_verified: str) -> list[dict]:
    html = fetch(SOURCE_URL)
    soup = BeautifulSoup(html, "html.parser")

    entries: list[dict] = []
    matched_tables = 0
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        if header != EXPECTED_HEADER:
            continue
        matched_tables += 1

        for row in rows[1:]:
            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
            if len(cells) != 5:
                continue
            model_name, input_cell, cache_cell, _storage_cell, output_cell = cells

            input_price = parse_usd(input_cell)
            output_price = parse_usd(output_cell)
            if input_price is None or output_price is None:
                # A row we can't price (e.g. malformed cell) — skip it rather
                # than write a wrong/zero price; the rest of the table still
                # produces good data.
                continue

            cache_read = parse_usd(cache_cell)
            cache_write = input_price if cache_read is not None else None

            entries.append(
                PriceEntry(
                    model=_model_id(model_name),
                    vendor=VENDOR,
                    input_price_usd=input_price,
                    output_price_usd=output_price,
                    cache_read_price_usd=cache_read,
                    cache_write_price_usd=cache_write,
                    source_url=SOURCE_URL,
                    last_verified=last_verified,
                ).to_dict()
            )

    if matched_tables == 0:
        raise RuntimeError(
            "zai_scraper: no table on the page matched the expected header "
            f"{EXPECTED_HEADER!r} — the page structure likely changed; "
            "needs a manual look, not a silent empty result."
        )
    if not entries:
        raise RuntimeError("zai_scraper: matched table(s) but extracted zero rows.")

    return entries
