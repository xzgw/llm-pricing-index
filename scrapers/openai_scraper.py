"""OpenAI pricing scraper.

Source: https://developers.openai.com/api/docs/pricing — this is the current
canonical URL as of 2026-07-03. Note: the historically-known
https://platform.openai.com/docs/pricing now issues an HTTP 301 permanent
redirect to this URL; that redirect was confirmed live (not assumed) before
picking this as SOURCE_URL.

Page structure (verified 2026-07-03): this is an Astro Islands site
(astro.build), not a plain server-rendered table and not a Next.js
`__NEXT_DATA__` blob. Each pricing table on the page is a client-hydrated
component (`<astro-island component-export="...">`) whose full dataset is
embedded as a JSON string in that element's `props` HTML attribute — present
in the static HTML, no JS execution required to read it.

Critically, the *rendered* `<table>` HTML for the flagship-models section is
silently truncated to its first 6 rows (presumably the component only SSRs a
preview and hydrates the rest client-side); the `props` JSON on the same
`<astro-island>` holds the complete list (42 rows for the standard tier, at
last check). A scraper reading only `<table><tr>` would silently produce a
~85%-incomplete dataset. So we parse the `props` JSON, not the rendered
`<table>`.

The flagship-models section renders four `TextTokenPricingTables` islands, one
per pricing *tier* — standard / batch / flex / priority — each holding the
same model list at different (tier-discounted or tier-surcharged) rates.
These are pricing modes for the same models, not separate models, so we take
only tier == "standard" (OpenAI's default, non-batch, non-priority per-token
API rate). We match on two independent structural signals so a positional
assumption (e.g. "first island") can't silently break this: (1) the parsed
`props` JSON's own `tier` key, and (2) the ancestor DOM wrapper's
`data-content-switcher-pane[data-value]` attribute — a value set independently
of the JSON blob itself.

Some flagship models (gpt-5.5, gpt-5.5-pro, gpt-5.4, gpt-5.4-pro) show a
"(<272K context length)" annotation on their model name — this is OpenAI's
short-context rate; the same page separately documents a "long context"
premium surcharge for prompts over that threshold. That's a per-request
pricing tier of the *same* model, not a distinct billable model id, so we
strip the annotation and record the short-context (base/default) rate.

"No published cache-read rate" appears in the source JSON as three different
sentinel forms depending on the row — `None` (JSON null), `""`, and `"-"` —
all handled identically via `base.parse_usd` (stringifying first). OpenAI
never publishes a distinct cache-*write*/creation rate anywhere on this page
(only a cache-*read* / "prompt caching" discount) — confirmed by grepping the
raw fetched HTML for cache-write language and finding none — so
cache_write_price_usd is always None for every entry here.

Scope: this scraper covers only the "Flagship models" text-token table (the
GPT/o-series chat/completions models billed per input+output token — the
schema this repo models). Explicitly NOT covered, because they don't fit a
flat input/output-per-token schema and would need fields this schema doesn't
have: Realtime/audio models (per-modality, some per-minute), image generation
models (per-image plus text tokens), video generation (Sora, per-second),
transcription (some per-minute), Tools (web search, per-call), Finetuning
(hourly training cost + separate inference rates). These all live in their
own `GroupedPricingTable`/`PricingTable` islands elsewhere on the same page
and can be added later as their own scrape targets if this schema grows to
support them.
"""

from __future__ import annotations

import json

from bs4 import BeautifulSoup

from .base import PriceEntry, fetch, parse_usd

SOURCE_URL = "https://developers.openai.com/api/docs/pricing"
VENDOR = "openai"
TARGET_COMPONENT = "TextTokenPricingTables"
TARGET_TIER = "standard"


def _model_id(display_name: str) -> str:
    """"gpt-5.5 (<272K context length)" -> "gpt-5.5" (strip the short-context
    annotation; see module docstring — it's a pricing tier, not a distinct
    model id).
    """
    return display_name.split(" (")[0].strip()


def _find_standard_tier_rows(soup: BeautifulSoup) -> list | None:
    """Returns the raw `rows` list from the TextTokenPricingTables island
    whose tier == "standard", verified against a second, independent DOM
    signal. Returns None if no island matches either signal (structural
    mismatch — caller raises).
    """
    for island in soup.find_all("astro-island"):
        if island.attrs.get("component-export") != TARGET_COMPONENT:
            continue
        props_raw = island.attrs.get("props")
        if not props_raw:
            continue
        try:
            props = json.loads(props_raw)
        except json.JSONDecodeError:
            continue

        tier_field = props.get("tier")
        tier = tier_field[1] if isinstance(tier_field, list) and len(tier_field) == 2 else None
        if tier != TARGET_TIER:
            continue

        # Second, independent structural signal: the ancestor content-switcher
        # pane's own data-value attribute (set in the DOM, not derived from
        # this same JSON blob) must agree.
        pane = island.find_parent(attrs={"data-content-switcher-pane": "true"})
        pane_value = pane.attrs.get("data-value") if pane else None
        if pane_value != TARGET_TIER:
            continue

        rows_field = props.get("rows")
        if not (isinstance(rows_field, list) and len(rows_field) == 2):
            continue
        return rows_field[1]

    return None


def scrape(last_verified: str) -> list[dict]:
    html = fetch(SOURCE_URL)
    soup = BeautifulSoup(html, "html.parser")

    rows = _find_standard_tier_rows(soup)
    if rows is None:
        raise RuntimeError(
            "openai_scraper: no astro-island[component-export="
            f"{TARGET_COMPONENT!r}] with tier={TARGET_TIER!r} (confirmed via "
            "both the props JSON and the data-content-switcher-pane "
            "data-value) was found — the page structure likely changed; "
            "needs a manual look, not a silent empty result."
        )

    entries: list[dict] = []
    for row in rows:
        # Each row is Astro's serialized-value wrapper: [1, [[0, cell], ...]].
        if not (isinstance(row, list) and len(row) == 2):
            continue
        cells = row[1]
        if not (isinstance(cells, list) and len(cells) == 4):
            continue
        try:
            model_name, input_cell, cache_cell, output_cell = (c[1] for c in cells)
        except (TypeError, IndexError):
            continue

        input_price = parse_usd(str(input_cell))
        output_price = parse_usd(str(output_cell))
        if input_price is None or output_price is None:
            # A row we can't price (malformed cell) — skip it rather than
            # write a wrong/zero price; the rest of the table still produces
            # good data.
            continue

        cache_read = parse_usd(str(cache_cell))

        entries.append(
            PriceEntry(
                model=_model_id(str(model_name)),
                vendor=VENDOR,
                input_price_usd=input_price,
                output_price_usd=output_price,
                cache_read_price_usd=cache_read,
                cache_write_price_usd=None,  # OpenAI publishes no cache-write rate.
                source_url=SOURCE_URL,
                last_verified=last_verified,
            ).to_dict()
        )

    if not entries:
        raise RuntimeError("openai_scraper: matched the standard-tier island but extracted zero rows.")

    return entries
