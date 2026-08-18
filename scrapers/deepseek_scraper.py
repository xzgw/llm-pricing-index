"""DeepSeek pricing scraper.

Source: https://api-docs.deepseek.com/quick_start/pricing/ (Docusaurus-hosted
docs site — server-rendered HTML, no JS execution needed to read it; the
historically-known `deepseek.com` docs URL 302-redirects here, confirmed
live before picking this as SOURCE_URL. NOT `deepseek.ai` — that domain is
not DeepSeek's own and shows up in third-party SEO pages, not the official
docs).

Verified page structure (2026-08-18): a single "Models & Pricing" page holds
one `<table>` listing every currently-served model as a COLUMN (not a row —
the opposite orientation from every other scraper in this repo). The header
row's first cell is literally "MODEL" and the remaining cells are the model
ids (currently `deepseek-v4-flash`, `deepseek-v4-pro`) — already lowercase-
hyphenated, no transform needed. Below that, each pricing-relevant fact is
its own row, matched by a *label cell* rather than position (rows below a
matched label inherit that label until the next one, DeepSeek's HTML expresses
this via table rowspan, which BeautifulSoup's plain row-by-row iteration
doesn't expand — so a row's own cell count varies depending on how many
leading label cells got "absorbed" by a previous row's rowspan). Because
of that, we don't rely on cell position at all: for each of the three rows
we care about, we take the LAST `len(models)` cells (the trailing numbers
are always fully present on every row; only the leading label cells are
elided by rowspan) rather than counting from the front.

DeepSeek publishes a genuine time-of-day price: PEAK (01:00-04:00 and
06:00-10:00 UTC, ~7h/day) is exactly 2x OFF-PEAK (the other ~17h/day). This
schema has no field for time-varying pricing, so — consistent with this
repo's precedent of quoting the standing/non-surcharge rate when a source
publishes a base rate plus a variable premium (see openai_scraper's
"standard" tier, google_scraper's first/lower tiered-context number) — we
record OFF-PEAK as the canonical price: it's the rate that applies most of
the day and carries no surcharge, whereas PEAK is explicitly described by
DeepSeek itself as a premium on top of it.

input_price_usd = "1M INPUT TOKENS (CACHE MISS)" off-peak (the real,
always-available rate with no caching in play — matches what every other
vendor's plain "Input" column means). cache_read_price_usd = "1M INPUT
TOKENS (CACHE HIT)" off-peak. output_price_usd = "1M OUTPUT TOKENS"
off-peak. DeepSeek's context caching is fully automatic (no explicit
create/write step or separate write-rate is published anywhere on this
page) — cache_write_price_usd is always None here, unlike z.ai's scraper
which infers a write rate.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import PriceEntry, fetch, parse_usd

SOURCE_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
VENDOR = "deepseek"


def _row_cells(row) -> list[str]:
    return [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]


def scrape(last_verified: str) -> list[dict]:
    html = fetch(SOURCE_URL)
    soup = BeautifulSoup(html, "html.parser")

    table = None
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if rows and _row_cells(rows[0])[:1] == ["MODEL"]:
            table = t
            break

    if table is None:
        raise RuntimeError(
            "deepseek_scraper: no table with a header row starting 'MODEL' "
            "found — the page structure likely changed; needs a manual "
            "look, not a silent empty result."
        )

    rows = table.find_all("tr")
    header = _row_cells(rows[0])
    model_ids = header[1:]
    n = len(model_ids)
    if n == 0:
        raise RuntimeError("deepseek_scraper: header row had no model columns.")

    cache_miss_row = cache_hit_row = output_row = None
    for row in rows[1:]:
        cells = _row_cells(row)
        joined = " | ".join(cells).upper()
        if "CACHE MISS" in joined and cache_miss_row is None:
            cache_miss_row = cells
        elif "CACHE HIT" in joined and cache_hit_row is None:
            cache_hit_row = cells
        elif any(c.upper() == "1M OUTPUT TOKENS" for c in cells) and output_row is None:
            output_row = cells

    if cache_miss_row is None or output_row is None:
        raise RuntimeError(
            "deepseek_scraper: could not find the expected 'CACHE MISS' "
            "input-price row and/or the '1M OUTPUT TOKENS' output-price "
            "row in the pricing table — the page structure likely changed."
        )

    def trailing_prices(row: list[str] | None) -> list[float | None]:
        if row is None or len(row) < n:
            return [None] * n
        return [parse_usd(c) for c in row[-n:]]

    input_prices = trailing_prices(cache_miss_row)
    output_prices = trailing_prices(output_row)
    cache_read_prices = trailing_prices(cache_hit_row)

    entries: list[dict] = []
    for i, model_id in enumerate(model_ids):
        input_price = input_prices[i]
        output_price = output_prices[i]
        if input_price is None or output_price is None:
            # A column we can't price (malformed cell) — skip it rather
            # than write a wrong/zero price; the rest of the table still
            # produces good data.
            continue

        entries.append(
            PriceEntry(
                model=model_id.strip(),
                vendor=VENDOR,
                input_price_usd=input_price,
                output_price_usd=output_price,
                cache_read_price_usd=cache_read_prices[i],
                cache_write_price_usd=None,  # DeepSeek publishes no cache-write rate.
                source_url=SOURCE_URL,
                last_verified=last_verified,
            ).to_dict()
        )

    if not entries:
        raise RuntimeError("deepseek_scraper: matched the pricing table but extracted zero usable entries.")

    return entries
