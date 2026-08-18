"""Alibaba Cloud Model Studio (Qwen) pricing scraper.

Source: https://www.alibabacloud.com/help/en/model-studio/model-pricing —
the **international**, USD-billed pricing reference. `help.aliyun.com/zh/
model-studio/model-pricing` is the mainland-China, RMB-billed twin of the
same page (same structure, different currency and, for many rows,
genuinely different numbers, not just a currency conversion). Per this
repo's schema (USD only, no currency conversion allowed — see README) we
use the `alibabacloud.com`/English/USD page. Both pages are server-rendered
HTML — no JS execution needed — confirmed by directly fetching each with
plain `curl`/`urllib` and finding real `<table>` markup already present;
an *earlier* attempt at `help.aliyun.com/zh/model-studio/models` (a
different, models-*capability*-overview page, not the pricing page) looked
like an empty JS shell — that was the wrong URL, not a JS-rendering
problem; the actual pricing page renders fine without a browser.

Scope: only the "Text generation - Qwen" and "Text generation - Qwen (open
source)" sections (everything between the "Text generation - Qwen" `<h2>`
and the "Text generation - third-party models" `<h2>` that immediately
follows them) — i.e. Alibaba's own Qwen chat/completion models, which is
what this schema (a flat per-1M-token input+output price) fits. Explicitly
NOT scraped, all for the same reason as this repo's openai/google
precedent (out of schema shape), verified by directly inspecting each
section's table structure before deciding, not assumed:
  - "Text generation - third-party models" (DeepSeek/Kimi/MiniMax/GLM
    resold via Bailian) — wrong vendor attribution; each of those has its
    own official-source scraper elsewhere in this repo.
  - "Image generation" (Qwen-Image, Wanx, ...) — verified these tables
    have a single "Output price" column billed per-*image*
    (e.g. "$0.075/image"), no input-token price at all.
  - "Text embedding" / "Multimodal embedding" / "Text reranking" — verified
    these tables have only a single "Input price" column and NO output
    price column at all (embeddings/reranking don't have output tokens).
    This schema requires both `input_price_usd` and `output_price_usd` as
    non-null numbers, so these can't be represented without either
    fabricating a fake $0 output price (misrepresenting "no such concept"
    as "free") or a schema change — an owner decision, not this scraper's
    to make. (This is where `text-embedding-v3`, `text-embedding-v4`, and
    `gte-rerank-v2` would have landed.)
  - Everything else (video/audio/speech/3D/industry models) — different
    non-token billing units, same reasoning as OpenAI's excluded
    Realtime/image/video sections.

Deployment scope: nearly every model is listed multiple times, once per
`Deployment scope` ("International", "Chinese mainland", "Hong Kong
(China)", "Global", "EU", "US", "Japan", ...), and verified these are NOT
just unit/currency variants of one price — e.g. qwen3-max is $1.2/$6 on
International/Hong Kong/EU but $0.359/$1.434 on Chinese mainland/Global.
We take only the row where `Deployment scope` is exactly "International"
(present for every "Text generation - Qwen" family checked, always listed
first) as the single canonical price, matching this repo's established
precedent of picking one standing variant when a source publishes several
for the same model (openai's "standard" tier, google's first/lower
number). Models offered only under other scope labels (seen: some Coder/
Omni/Translation variants use "Global"/"US"/"EU"/"Japan" scope names
instead of "International") are not included in this pass — a real
coverage gap, not a bug, flagged in this repo's own report rather than
silently guessed at.

Every model's Model ID cell also carries free-text annotations in the same
cell, e.g. "qwen3-max Currently equivalent to qwen3-max-2026-01-23 context
caching discount" or "qwen-max 50% batch inference discount" — the actual
model id is always the first whitespace-delimited token; every dated/
pinned snapshot id (e.g. "qwen3-max-2026-01-23") is ALSO its own separate
table row with its own Model ID cell, so both the "current alias" and each
dated pin end up as distinct entries (both are genuinely independent,
individually billable model ids per Alibaba's own docs, and either might
be what's actually configured on a downstream gateway).

Two structural complications, both verified against real fetched rows
before writing this, not assumed:
  1. Context-length-tiered pricing: some model tables use HTML rowspan so
     a model's first price bracket (e.g. "0<Token<=32K") is a full row
     (Model ID + Deployment scope + ... + price cells) while its
     additional brackets (e.g. "32K<Token<=128K") are SHORT continuation
     rows missing the rowspanned leading cells. Rather than trying to
     reconstruct rowspan alignment, we only process "full" rows (cell
     count == the table's expected full-row length) and skip short
     continuation rows entirely — equivalent to always taking the
     first/lowest bracket, consistent with google_scraper's tiered-pricing
     precedent.
  2. Two-row headers: some tables (the "-Plus" family in particular) split
     "Output price" into a Non-Thinking/Thinking-mode sub-header on a
     SECOND header row (`rows[1]`, itself shorter than `rows[0]` and
     containing no `$`/token-bracket text — that's how we detect it's a
     sub-header and not a data row). We don't need to reconstruct which
     column is which afterward: a subdivided column's first sub-column
     always lands at the exact same cell INDEX the single undivided column
     would have occupied (subdivision only ever replaces one column with
     several, in place), so `output_idx` computed from the plain first
     header row still points at the right cell in every full data row —
     we just widen "expected full-row length" by
     `len(row0) - 1 + len(row1)` when a sub-header is detected, and start
     reading data at `rows[2:]` instead of `rows[1:]`. We take the FIRST
     sub-column as canonical (Non-Thinking mode where that's the split;
     one Omni-family table splits into 4 modality-based sub-columns
     instead — same "take first" rule applies generically there too),
     consistent with this repo's "take the first/base/non-premium variant"
     precedent used throughout.

Some price cells read e.g. "List price $2.5 Limited-time 50% off" — a
temporary promotional discount layered on the list price (contrast this
repo's minimax_scraper, which takes the *discounted* number for a cell
explicitly marked "Permanent" — here it's explicitly "Limited-time", i.e.
could lapse). We record the List price (the leftmost/first number in the
cell — `parse_usd` already returns the first regex match) as the standing,
non-promotional rate. This means the number recorded here can be HIGHER
than what a customer is actually billed today for as long as the
promotion runs — flagged here and in this repo's own report for the owner
to weigh in on, same as the tiered-pricing and deployment-scope choices
above.

No cache-read/cache-write rate is published as its own column anywhere in
these tables — Alibaba applies context-caching as a discount folded into
the headline price (the "context caching discount" annotation text seen on
many Model ID cells) rather than a separately tabulated rate here; a
dedicated cache-pricing page exists but is out of scope for this pass.
cache_read_price_usd and cache_write_price_usd are always None.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import PriceEntry, fetch, parse_usd

SOURCE_URL = "https://www.alibabacloud.com/help/en/model-studio/model-pricing"
VENDOR = "alibaba"
SECTION_START_HEADING = "Text generation - Qwen"
SECTION_STOP_HEADING = "Text generation - third-party models"
CANONICAL_DEPLOYMENT_SCOPE = "International"


def _row_cells(row) -> list[str]:
    return [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]


def _looks_like_subheader(cells: list[str], header_len: int) -> bool:
    if not (2 <= len(cells) < header_len):
        return False
    if any("$" in c for c in cells):
        return False
    if any(("<token" in c.lower() or "≤" in c or "no tiered pricing" in c.lower()) for c in cells):
        return False
    return True


def _col_index(header: list[str], predicate) -> int | None:
    for i, title in enumerate(header):
        if predicate(title.lower()):
            return i
    return None


def _tables_in_scope(soup: BeautifulSoup) -> list:
    start = stop = None
    for h in soup.find_all("h2"):
        text = h.get_text(strip=True)
        if text == SECTION_START_HEADING and start is None:
            start = h
        elif text == SECTION_STOP_HEADING and start is not None:
            stop = h
            break
    if start is None:
        return []

    tables = []
    node = start
    while True:
        node = node.find_next()
        if node is None or node is stop:
            break
        if node.name == "table":
            tables.append(node)
    return tables


def scrape(last_verified: str) -> list[dict]:
    html = fetch(SOURCE_URL)
    soup = BeautifulSoup(html, "html.parser")

    tables = _tables_in_scope(soup)
    if not tables:
        raise RuntimeError(
            f"alibaba_scraper: no {SECTION_START_HEADING!r} ... "
            f"{SECTION_STOP_HEADING!r} section found — the page structure "
            "likely changed; needs a manual look, not a silent empty result."
        )

    entries: dict[str, dict] = {}
    matched_tables = 0

    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue
        header = _row_cells(rows[0])
        model_idx = _col_index(header, lambda t: t.startswith("model id"))
        deployment_idx = _col_index(header, lambda t: t.startswith("deployment scope"))
        input_idx = _col_index(header, lambda t: t.startswith("input price"))
        output_idx = _col_index(header, lambda t: t.startswith("output price"))
        if None in (model_idx, deployment_idx, input_idx, output_idx):
            continue
        matched_tables += 1

        data_start = 1
        expected_len = len(header)
        if len(rows) >= 2:
            row1_cells = _row_cells(rows[1])
            if _looks_like_subheader(row1_cells, len(header)):
                expected_len = len(header) - 1 + len(row1_cells)
                data_start = 2

        for row in rows[data_start:]:
            cells = _row_cells(row)
            if len(cells) != expected_len:
                continue  # a tiered-pricing continuation row — skip (see docstring)
            if cells[deployment_idx] != CANONICAL_DEPLOYMENT_SCOPE:
                continue

            model_tokens = cells[model_idx].split()
            if not model_tokens:
                continue
            model_id = model_tokens[0]

            input_price = parse_usd(cells[input_idx])
            output_price = parse_usd(cells[output_idx])
            if input_price is None or output_price is None:
                continue

            entries[model_id] = PriceEntry(
                model=model_id,
                vendor=VENDOR,
                input_price_usd=input_price,
                output_price_usd=output_price,
                cache_read_price_usd=None,
                cache_write_price_usd=None,
                source_url=SOURCE_URL,
                last_verified=last_verified,
            ).to_dict()

    if matched_tables == 0:
        raise RuntimeError(
            "alibaba_scraper: found the target sections but no table had "
            "both a 'Model ID'/'Deployment scope' and 'Input price'/"
            "'Output price' column — the page structure likely changed."
        )
    if not entries:
        raise RuntimeError(
            "alibaba_scraper: matched table(s) but extracted zero rows "
            f"with Deployment scope == {CANONICAL_DEPLOYMENT_SCOPE!r}."
        )

    return list(entries.values())
