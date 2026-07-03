"""Anthropic (Claude) pricing scraper.

Source: https://platform.claude.com/docs/en/about-claude/pricing (Claude
Platform Docs — server-rendered HTML, no JS execution needed). This is the
first-party API pricing reference; NOT https://claude.com/pricing, which is
the consumer subscription page (Pro/Max/Team/Enterprise plans, title "Plans &
Pricing | Claude by Anthropic") and has no per-token API rates. The old
docs.anthropic.com/en/docs/about-claude/pricing URL now 301s to this one
(platform.claude.com), confirming the docs-site migration.

Verified page structure (2026-07-03): the page has 13 semantic <table>
elements covering many products (model pricing, AWS/Microsoft Foundry cloud
pricing, prompt caching multipliers, fast mode, batch, long context, tool
use, Claude Managed Agents, worked examples, ...). We match ONLY the table
whose header equals EXPECTED_HEADER below — this is the table under the
"## Model pricing" h2, preceded by the literal lead-in "The following table
shows pricing for all Claude models:". It is the single canonical per-model
per-token table on the page.

Two other tables have a superficially similar "Model" first column and are
deliberately NOT matched (verified by header text, so they can never be
confused with the real one even if their row content changes):
  - "### Fast mode pricing" table, header ['Model', 'Input', 'Output'] — a
    premium-speed research-preview add-on for Opus 4.8/4.7 only, not standard
    per-token pricing.
  - "### Batch processing" table, header ['Model', 'Batch input', 'Batch
    output'] — the batch API's flat 50%-off discount, expressed per-model
    only because Anthropic tabulates it that way; it's a multiplier on the
    standard rate, not a distinct billable model. Per this task's explicit
    scope note, batch is intentionally excluded as "a discount multiplier,
    not a distinct model row."
"### Long context pricing" has no table at all — its prose confirms models
with a 1M-token context window are billed at the SAME per-token rate
regardless of request length, so there is no separate row to add for it
either.

Row-name annotation handling (verified against real cells, not assumed):
  - Most rows are a bare model name, e.g. "Claude Opus 4.8".
  - Status-qualified rows use a parenthetical suffix on the same text node,
    e.g. "Claude Opus 4.1 (deprecated)", "Claude Opus 4 (retired, except on
    Google Cloud)", "Claude Mythos 5 (limited availability)".
  - Exactly one model (Claude Sonnet 5, as of this verification) has a date-
    qualified pair of rows separated by a real <br/> tag rather than a
    parenthetical: "Claude Sonnet 5<br/>through August 31, 2026" (introductory
    rate, currently active) and "Claude Sonnet 5<br/>starting September 1,
    2026" (future rate, not yet effective). We detect the <br/>-qualifier
    style structurally via get_text(separator="|"): a second "|"-delimited
    segment starting with "starting " marks a not-yet-effective future row,
    which we skip (a last_verified snapshot should record today's effective
    price, not a scheduled future one); "through "-prefixed segments are the
    currently-active row and are kept using the bare base model name.

Model inclusion (this run): kept all bare and "limited availability" rows —
Claude Fable 5, Claude Mythos 5, Opus 4.8/4.7/4.6/4.5, Sonnet 5 (introductory
rate), Sonnet 4.6/4.5, Haiku 4.5. Excluded rows explicitly marked
"deprecated" or "retired" by Anthropic itself (Opus 4.1, Opus 4, Sonnet 4,
Haiku 3.5) — the task scope is "the primary Claude model family" / "current
lineup", and Anthropic's own annotation is the clearest live signal for
what that is (retired models are also only billable on third-party clouds
per their own annotation text, not first-party API).
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from .base import PriceEntry, fetch, parse_usd

SOURCE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
VENDOR = "anthropic"
EXPECTED_HEADER = [
    "Model",
    "Base Input Tokens",
    "5m Cache Writes",
    "1h Cache Writes",
    "Cache Hits & Refreshes",
    "Output Tokens",
]
EXCLUDED_STATUS_KEYWORDS = ("deprecated", "retired")


def _model_id(display_name: str) -> str:
    """"Claude Opus 4.8" -> "claude-opus-4-8" (dots to hyphens, matches this
    repo's established model-id convention, e.g. "claude-haiku-4-5")."""
    return display_name.strip().lower().replace(".", "-").replace(" ", "-")


def _split_name_cell(cell) -> tuple[str, str]:
    """Returns (base_model_name, qualifier). qualifier is '' for a bare row,
    the parenthetical text (without parens) for a status-qualified row, or
    the <br/>-separated date text for the Sonnet-5-style dated pair.
    """
    raw = cell.get_text(separator="|", strip=True)
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    base = parts[0].rstrip("(").strip() if parts else ""
    qualifier = parts[1].rstrip(")").strip() if len(parts) > 1 else ""
    return base, qualifier


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
            cells_raw = row.find_all(["th", "td"])
            if len(cells_raw) != 6:
                continue

            base_name, qualifier = _split_name_cell(cells_raw[0])
            if not base_name:
                continue
            if qualifier.startswith("starting "):
                # A future-dated rate that isn't effective yet (e.g. "Claude
                # Sonnet 5" / "starting September 1, 2026") — skip; the
                # currently-active row for the same model id is a separate
                # table row and is handled on its own iteration.
                continue
            if any(kw in qualifier.lower() for kw in EXCLUDED_STATUS_KEYWORDS):
                continue

            texts = [c.get_text(strip=True) for c in cells_raw[1:]]
            base_input_cell, cache_5m_cell, cache_1h_cell, cache_hit_cell, output_cell = texts

            input_price = parse_usd(base_input_cell)
            output_price = parse_usd(output_cell)
            if input_price is None or output_price is None:
                # A row we can't price (e.g. malformed cell) — skip it rather
                # than write a wrong/zero price; the rest of the table still
                # produces good data.
                continue

            cache_read = parse_usd(cache_hit_cell)
            # Anthropic publishes two distinct cache-write rates (5-minute
            # and 1-hour TTL). This schema has one cache_write_price_usd
            # field, so we use the 5-minute rate — it's the default/standard
            # cache TTL and the one every cacheable model publishes; the 1h
            # rate is an opt-in extended-TTL variant on top of it.
            cache_write = parse_usd(cache_5m_cell)

            entries.append(
                PriceEntry(
                    model=_model_id(base_name),
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
            "anthropic_scraper: no table on the page matched the expected "
            f"header {EXPECTED_HEADER!r} — the page structure likely "
            "changed; needs a manual look, not a silent empty result."
        )
    if not entries:
        raise RuntimeError("anthropic_scraper: matched table(s) but extracted zero rows.")

    return entries
