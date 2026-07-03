"""Google (Gemini) pricing scraper.

Source: https://ai.google.dev/gemini-api/docs/pricing (Google devsite —
server-rendered HTML, no JS execution needed). Verified 2026-07-03: fetching
with a bare `curl`/`urllib` gets stuck in an "auto sign-in" redirect loop
(devsite issues a `Skip AutoSignin False` cookie dance through
accounts.google.com even for anonymous visitors); a normal browser resolves
this via its cookie jar in a few hops. `fetch()` in base.py uses a plain
`urllib.request` call with no cookie jar and no redirect handling — it works
here anyway because `urllib.request.urlopen` follows the *same* redirect
chain a browser would and, empirically, terminates (unlike `curl -L`'s
default of blindly re-sending with no cookies, which loops until
`--max-redirs`). If this ever starts raising a redirect/urlopen error, that's
almost certainly this login-redirect dance regressing, not a real structural
change — the fix is a `http.cookiejar`-backed opener, not a parsing fix.

Verified page structure (2026-07-03): each model is a
`<div class="models-section">` containing an `<h2>` (display name) and a
`<code>` (the model id, e.g. "gemini-2.5-flash"). The pricing table
immediately follows — either wrapped in
`<devsite-selector><section><h3>Standard</h3><table>...` (models offered
across multiple API serving tiers: Standard / Batch / Flex / Priority) or as
a bare `<table>` directly (models with only one tier). We always take the
*first* table found (== "Standard" tier when tiers exist), matching this
repo's z.ai-scraper precedent of quoting the standard non-discounted rate as
canonical.

Each pricing table is **transposed** relative to z.ai's: one row per priced
dimension, with the header `['', 'Free Tier', 'Paid Tier, per 1M tokens in
USD']`. Row labels are NOT identical across models (seen: "Input price",
"Input price (text, image, video)", "Text input price" for embeddings) so we
match on a lowercased *prefix* per field rather than an exact label, and we
gate the whole table on the header's 3rd cell reading exactly
"Paid Tier, per 1M tokens in USD" — this is what actually separates
per-token-billed models from Imagen/Veo/Lyria (billed "per Image"/"per
second"/"per request" — same devsite template, different unit, correctly
out of scope for this repo's per-1M-token schema) without hardcoding a model
name allowlist.

Tiered (context-length-dependent) pricing: Gemini 2.5 Pro and Gemini 3.x Pro
models publish TWO input/output/cache prices, e.g. "$1.25, prompts <= 200k
tokens $2.50, prompts > 200k tokens". `schema/prices.schema.json` has no
field for this. We take the FIRST (lower / <=200k) number as the canonical
price via `parse_usd` (which regexes out the first numeric token) and rely
on this docstring + the report to flag it — see this repo's README/schema
if a `input_price_usd_over_200k`-style field gets added later. A second,
unrelated multi-price case appears on Gemini 2.0 Flash/Flash-Lite: separate
rates by input *modality* ("$0.10 (text/image/video) $0.70 (audio)") rather
than context length; we take the same first-number (text) rate for the same
reason, and it isn't context-length tiering so doesn't need a new schema
field, just a note that it's the text/image/video rate, not audio.

Deliberately excluded (see the actual filtering logic in `scrape()` for how
each is really caught — this is a summary, not a hardcoded name list):
  - Imagen 4, Veo 3.1/3/2, Lyria 3: priced per-image / per-second / per-
    request, not per-token — filtered by the "per 1M tokens" header gate.
  - Gemini Embedding / Gemini Embedding 2: embeddings have an input price
    but no output price at all (different pricing shape) — filtered by our
    require-both-input-and-output-price check.
  - Gemma 4: open-weight model; every price cell in its table (input,
    output, caching, tuning) reads "Not available" — filtered by our
    require-both-input-and-output-price check the same as embeddings.
  - Gemini 2.5 Flash Image ("Nano Banana" v1) ONLY: its output row is
    "$0.039 per image*" with NO per-token component published at all —
    filtered by `_LEADING_PRICE_IS_PER_IMAGE` (a regex anchored at the
    start of the cell, not a plain substring check — see below for why
    that distinction matters).
  - Not model sections at all (skipped because they aren't
    `models-section` divs): "Pricing for tools", "Pricing for agents",
    "Notes".

Image-output models are NOT uniformly excluded — this needed care. The
*newer* image models (gemini-3.1-flash-image, gemini-3.1-flash-lite-image,
gemini-3-pro-image, i.e. "Nano Banana 2"/"Nano Banana Pro") publish a
genuinely dual-rate output row, e.g. "$3 (text and thinking) $60.00
(images) Equivalent to $0.045 per 0.5K image...". The FIRST number there
(`$3`) is a real, distinct per-1M-token rate for text/thinking output
tokens — Google publishes it as such — with the per-image figure only
appearing later in the same cell describing the image-generation
component. That's a materially different shape from the older
gemini-2.5-flash-image, whose output row is JUST "$0.039 per image*" with
no separate token rate at all. So these three newer models ARE included
here (with that leading per-token number as output_price_usd), while only
the older one is excluded — `_LEADING_PRICE_IS_PER_IMAGE` distinguishes the
two shapes by anchoring "per image" immediately after the leading `$`
amount (present in the v1 case, absent in the v2/v3 cases where "per
image" only shows up describing a later number in the same cell). A plain
`"per image" in cell` substring check would have wrongly excluded all four
— this was caught during live testing, not assumed up front.

Included despite being non-obvious "Gemini" family members, because they DO
carry genuine per-1M-token input+output pricing on this same official page:
Gemini 3.5 Live Translate, Gemini 2.5 Flash Native Audio (Live API), Gemini
2.5 Computer Use Preview, the TTS models (audio output billed as audio
*tokens*, same unit, just a much higher per-token rate), and Gemini
Robotics-ER 1.6 Preview.

No distinct cache-write rate is published anywhere on this page (grepped the
full page text for "cache writ"/"cache creation"/"write price" — no hits),
so cache_write_price_usd is always None here, unlike this repo's z.ai
scraper which infers cache_write == input_price. Inventing a number Google
doesn't publish would misrepresent the source.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .base import PriceEntry, fetch, parse_usd

SOURCE_URL = "https://ai.google.dev/gemini-api/docs/pricing"
VENDOR = "google"
EXPECTED_PAID_TIER_HEADER = "Paid Tier, per 1M tokens in USD"

# Matches a leading "$<number>" immediately followed (allowing a little
# whitespace/punctuation) by "per image" — e.g. "$0.039 per image*". This is
# how gemini-2.5-flash-image's output row is written: the FIRST price in the
# cell is itself a per-image count, not a per-token rate, so parse_usd would
# otherwise silently return a per-image number mislabeled as a per-1M-token
# price. Contrast with the newer image models (gemini-3.1-flash-image etc.),
# whose output row reads e.g. "$3 (text and thinking) $60.00 (images)..." —
# a genuine per-token text-output rate comes FIRST there, with the per-image
# figure only appearing later in the same cell, so this pattern (anchored at
# the start) correctly leaves those alone.
_LEADING_PRICE_IS_PER_IMAGE = re.compile(r"^\s*\$[\d.,]+\s*per\s+image", re.IGNORECASE)


def _model_id(code_text: str) -> str:
    """The <code> element under each model's heading is already the exact
    lowercase-hyphenated model id (e.g. "gemini-2.5-flash") — no transform
    needed beyond stripping whitespace.
    """
    return code_text.strip()


def _first_pricing_table(models_section):
    """Walk forward from a `models-section` div to find the first `<table>`
    that follows it — whether it's a bare table or nested inside a
    `<devsite-selector><section>` (the "Standard" tier, always listed
    first). Stops and returns None if the next `models-section` is reached
    first (== this model has no pricing table at all, e.g. Live API native
    audio).
    """
    node = models_section
    hops = 0
    while True:
        node = node.find_next_sibling()
        hops += 1
        if node is None or hops > 6:
            return None
        if node.name == "div" and node.get("class") and "models-section" in node.get("class"):
            return None
        table = node if node.name == "table" else node.find("table")
        if table is not None:
            return table


def _row_label_and_paid_cell(row) -> tuple[str, str] | None:
    cells = row.find_all(["th", "td"])
    if len(cells) < 3:
        return None
    return cells[0].get_text(" ", strip=True), cells[2].get_text(" ", strip=True)


def scrape(last_verified: str) -> list[dict]:
    html = fetch(SOURCE_URL)
    soup = BeautifulSoup(html, "html.parser")

    entries: list[dict] = []
    model_sections_seen = 0
    per_token_tables_seen = 0

    for h2 in soup.find_all("h2"):
        models_section = h2.parent.parent if h2.parent else None
        if not (
            models_section is not None
            and models_section.name == "div"
            and models_section.get("class")
            and "models-section" in models_section.get("class")
        ):
            continue  # not a model heading at all (e.g. "Notes", "Pricing for tools")
        model_sections_seen += 1

        code_el = models_section.find("code")
        if code_el is None:
            continue  # heading with no model id — not a priced model row
        model_id = _model_id(code_el.get_text())

        table = _first_pricing_table(models_section)
        if table is None:
            continue  # e.g. Live API native audio — no table follows at all

        rows = table.find_all("tr")
        if not rows:
            continue
        header_cells = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
        if len(header_cells) < 3 or header_cells[2] != EXPECTED_PAID_TIER_HEADER:
            # Imagen/Veo/Lyria share this exact template but are priced
            # "per Image"/"per second"/"per request" — out of scope for a
            # per-1M-token schema, not a scraper bug.
            continue
        per_token_tables_seen += 1

        input_price = output_price = cache_read = None
        output_is_per_image_only = False
        for row in rows[1:]:
            parsed = _row_label_and_paid_cell(row)
            if parsed is None:
                continue
            label, paid_cell = parsed
            label_l = label.lower()
            if label_l.startswith("input price") or label_l.startswith("text input price"):
                input_price = parse_usd(paid_cell)
            elif label_l.startswith("output price"):
                if _LEADING_PRICE_IS_PER_IMAGE.match(paid_cell):
                    output_is_per_image_only = True
                else:
                    output_price = parse_usd(paid_cell)
            elif label_l.startswith("context caching price"):
                cache_read = parse_usd(paid_cell)

        if output_is_per_image_only:
            # gemini-2.5-flash-image ("Nano Banana" v1): output row is
            # "$0.039 per image*" — no per-token rate published at all, out
            # of scope for this per-1M-token schema. (Newer image models
            # like gemini-3.1-flash-image DO publish a genuine per-token
            # text-output rate alongside a per-image figure — those are
            # handled by the regex only matching a *leading* per-image
            # price, so they fall through to the normal parse_usd() below.)
            continue
        if input_price is None or output_price is None:
            # Embeddings (no output price at all) and Gemma 4 (every paid-
            # tier cell reads "Not available") land here. Skip rather than
            # write a partial/wrong entry.
            continue

        entries.append(
            PriceEntry(
                model=model_id,
                vendor=VENDOR,
                input_price_usd=input_price,
                output_price_usd=output_price,
                cache_read_price_usd=cache_read,
                cache_write_price_usd=None,  # Google publishes no distinct cache-write rate.
                source_url=SOURCE_URL,
                last_verified=last_verified,
            ).to_dict()
        )

    if model_sections_seen == 0:
        raise RuntimeError(
            "google_scraper: found zero `models-section` divs at all — the "
            "page structure has likely changed; needs a manual look."
        )
    if per_token_tables_seen == 0:
        raise RuntimeError(
            "google_scraper: found model sections but none had a table with "
            f"header {EXPECTED_PAID_TIER_HEADER!r} — the page structure "
            "likely changed; needs a manual look, not a silent empty result."
        )
    if not entries:
        raise RuntimeError(
            "google_scraper: matched per-token pricing tables but extracted "
            "zero usable entries — needs a manual look."
        )

    return entries
