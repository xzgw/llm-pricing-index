"""MiniMax pricing scraper.

Source: https://platform.minimax.io/docs/guides/pricing-paygo (Mintlify-
hosted docs — `minimax.io` is the international, USD-billed platform;
`platform.minimaxi.com` is the mainland-China, RMB-billed twin. Per this
repo's schema (USD only, no currency conversion — see README) we use the
`.io` host).

Like this repo's kimi/moonshot scraper, this site's rendered HTML has no
server-side `<table>` markup — but unlike Kimi's proprietary MDX
`<DocTable rows={[...]}>` component, MiniMax's Mintlify docs use genuine
GFM pipe-table markdown (`| Model | Input | Output | ... |`), fetched from
the same documented Mintlify `<path>.md` convenience endpoint (verified
live: returns 200 with real markdown source). We parse that directly as
markdown tables rather than reaching for a full markdown-to-HTML renderer —
the table shape here is a simple, regular grid with no rowspan/colspan
equivalent to worry about.

Page layout (verified 2026-08-18): the `## LLM` section holds three pricing
tables for the current chat/completion model lineup:
  1. A `<Tabs>` block with a `<Tab title="Standard">` and a
     `<Tab title="Priority*">` variant of the same table (Priority is a
     1.5x-priced faster-response service tier for the same model — a
     request-time flag, not a distinct model id). We keep only the
     Standard tab, matching this repo's established "standard tier"
     precedent (see openai_scraper). Implementation: the whole `<Tab
     title="Priority...">...</Tab>` block is stripped from the section text
     before any table is parsed, so the Priority table is structurally
     never seen rather than filtered post-hoc.
  2. A plain (non-tabbed) table for the current non-M3 model family.
  3. A `<Accordion title="Legacy Models">` table for older-generation
     models. These are still genuinely priced and served (not marked
     deprecated/retired the way this repo's anthropic_scraper's excluded
     rows are) — included.
All three tables share a compatible column set (`Model`, `Input`, `Output`,
`Prompt caching Read`, optionally `Prompt caching Write` — the M3 table
lacks a Write column entirely; handled by column-title matching, not a
fixed column count, so a missing column just yields `None` rather than a
misalignment).

Two cell-content quirks, both handled generically rather than by table:
  - Tiered-by-input-length pricing: MiniMax-M3 publishes two full rows,
    "<=512k input tokens" and ">512k input tokens", each a fully separate
    price. Consistent with this repo's precedent for tiered pricing
    (google_scraper takes the first/lower-tier number), we keep only the
    first-seen row per model id and skip later rows for an id already
    recorded — the <=512k (base/lower) tier is listed first in every case
    observed.
  - Permanent-discount strikethrough: some cells read literally
    `~~$0.60~~ $0.30 / M tokens` — MiniMax's own markup for "list price,
    strikethrough, followed by the actual current price", explicitly
    labeled "Permanent ... off" on this page (not a limited-time promo —
    contrast this repo's alibaba_scraper, which deliberately keeps the
    *list* price for a "Limited-time X% off" cell because that discount is
    time-boxed and could lapse). Since the discount here is described as
    permanent, the struck-through number is stripped out (regex removal of
    the `~~...~~` span) and the remaining, undiscounted-looking number —
    which is actually the one MiniMax bills today — is what we record.

Audio/Video/Music/Image/MCP/Server-Tools pricing (the rest of this same
page, per-character/per-second/per-image/per-request) is out of scope for
this per-1M-token schema, the same reasoning openai_scraper uses to exclude
Realtime/audio/image models — we simply never look past the `## LLM`
section's own end (the next `## ` heading, `## Audio`).
"""

from __future__ import annotations

import re

from .base import PriceEntry, fetch, parse_usd

SOURCE_URL = "https://platform.minimax.io/docs/guides/pricing-paygo"
VENDOR = "minimax"

_LLM_SECTION_RE = re.compile(r"^## LLM\n(.*?)\n## ", re.S | re.M)
_PRIORITY_TAB_RE = re.compile(r'<Tab title="Priority[^"]*">.*?</Tab>', re.S)
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
_STRIKETHROUGH_RE = re.compile(r"~~[^~]*~~")


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-+:?", c) for c in cells)


def _find_table_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.split("\n"):
        if _TABLE_LINE_RE.match(line):
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _model_id_from_cell(cell: str) -> str:
    # Model cells look like "**MiniMax-M3**<br />≤ 512k input tokens <span
    # ...>...</span>" — the bare model id is the bold text before any <br
    # or <span markup.
    base = re.split(r"<br|<span", cell)[0]
    return base.replace("*", "").strip().lower()


def _col_index(header: list[str], predicate) -> int | None:
    for i, title in enumerate(header):
        if predicate(title.lower()):
            return i
    return None


def scrape(last_verified: str) -> list[dict]:
    md = fetch(SOURCE_URL + ".md")

    section_match = _LLM_SECTION_RE.search(md)
    if not section_match:
        raise RuntimeError(
            "minimax_scraper: no '## LLM' section found in the pricing "
            "page's markdown source — the page structure likely changed; "
            "needs a manual look, not a silent empty result."
        )
    section = _PRIORITY_TAB_RE.sub("", section_match.group(1))

    blocks = _find_table_blocks(section)
    if not blocks:
        raise RuntimeError("minimax_scraper: found the '## LLM' section but no markdown tables inside it.")

    entries: list[dict] = []
    seen_models: set[str] = set()
    tables_matched = 0

    for block in blocks:
        if len(block) < 3:
            continue
        header = _split_row(block[0])
        if not _is_separator_row(_split_row(block[1])):
            continue

        model_idx = _col_index(header, lambda t: t.startswith("model"))
        input_idx = _col_index(header, lambda t: t.startswith("input") and "caching" not in t)
        output_idx = _col_index(header, lambda t: t.startswith("output"))
        cache_read_idx = _col_index(header, lambda t: "caching read" in t)
        cache_write_idx = _col_index(header, lambda t: "caching write" in t)
        if model_idx is None or input_idx is None or output_idx is None:
            continue
        tables_matched += 1

        for line in block[2:]:
            cells = _split_row(line)
            if len(cells) != len(header):
                continue
            model_id = _model_id_from_cell(cells[model_idx])
            if not model_id or model_id in seen_models:
                continue  # first-seen row per model wins (base/lower tier)

            input_price = parse_usd(_STRIKETHROUGH_RE.sub("", cells[input_idx]))
            output_price = parse_usd(_STRIKETHROUGH_RE.sub("", cells[output_idx]))
            if input_price is None or output_price is None:
                continue

            cache_read = (
                parse_usd(_STRIKETHROUGH_RE.sub("", cells[cache_read_idx])) if cache_read_idx is not None else None
            )
            cache_write = (
                parse_usd(_STRIKETHROUGH_RE.sub("", cells[cache_write_idx])) if cache_write_idx is not None else None
            )

            seen_models.add(model_id)
            entries.append(
                PriceEntry(
                    model=model_id,
                    vendor=VENDOR,
                    input_price_usd=input_price,
                    output_price_usd=output_price,
                    cache_read_price_usd=cache_read,
                    cache_write_price_usd=cache_write,
                    source_url=SOURCE_URL,
                    last_verified=last_verified,
                ).to_dict()
            )

    if tables_matched == 0:
        raise RuntimeError(
            "minimax_scraper: found tables in the '## LLM' section but none "
            "had both a 'Model' and an 'Input'/'Output' column — the page "
            "structure likely changed."
        )
    if not entries:
        raise RuntimeError("minimax_scraper: matched table(s) but extracted zero usable entries.")

    return entries
