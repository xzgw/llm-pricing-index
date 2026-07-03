# llm-pricing-index

A public, machine-readable index of official LLM API pricing, refreshed
weekly by scraping each vendor's own pricing page.

Built to feed [routed](https://github.com/xzgw)'s "sync official prices"
feature (a console button that pulls this repo's `prices.json` straight from
`raw.githubusercontent.com` into a price-rule batch-import preview), but the
data is generic and anyone is welcome to consume it the same way.

## Data: `prices.json`

Root-level JSON array, one entry per model. Schema: [`schema/prices.schema.json`](schema/prices.schema.json).

```json
{
  "model": "glm-4.7",
  "vendor": "z.ai",
  "input_price_usd": 0.6,
  "output_price_usd": 2.2,
  "cache_read_price_usd": 0.11,
  "cache_write_price_usd": 0.6,
  "source_url": "https://docs.z.ai/guides/overview/pricing",
  "last_verified": "2026-07-03"
}
```

All prices are **USD per 1,000,000 tokens**. `cache_read_price_usd` /
`cache_write_price_usd` are omitted (`null`) when a vendor doesn't publish a
distinct rate for that model. There is no `cache_hit_price` field — that's a
routed-specific semantic-cache concept, not part of any vendor's official
pricing.

**No history in this file.** `prices.json` always holds only the current
confirmed values. Historical price changes are tracked via `git log` on this
file, not a separate history array or table.

## How it's updated

`.github/workflows/update-prices.yml` runs `run.py` **weekly** (Monday cron).
`run.py` calls each vendor's scraper (`scrapers/<vendor>_scraper.py`)
independently, merges fresh results into `prices.json`, and — if anything
changed — the workflow opens a **pull request** (never pushes to `main`
directly). The PR description lists every change, model-by-model, old → new.
A human reviews and merges.

### Failure isolation

Vendors covered today: OpenAI, Anthropic, Google (Gemini), z.ai (GLM) — see
`scrapers/`. Others can be added the same way as needed.

Each vendor's scraper runs in its own `try/except` in `run.py`. If one
vendor's scraper breaks (a pricing page's HTML structure changed, a network
error, etc.), that vendor's **existing** entries in `prices.json` are left
completely untouched — the failure is reported in the workflow run and the
(if any) PR body, but it never blocks or corrupts the other vendors' updates.
A scraper that can't find what it expects raises loudly rather than silently
returning nothing or a wrong number — see `scrapers/zai_scraper.py`'s
docstring for the reasoning.

### Sanity checks

Before a freshly-scraped price is accepted, `scrapers/sanity.py` checks it
isn't negative, isn't missing, and hasn't jumped more than 10x versus the
previous known value in either direction (a jump that large is far more
likely a scraper bug — wrong column, wrong units — than a real vendor price
change). A negative/missing price is dropped and the old value is kept; a
large jump is flagged in the change summary but not auto-rejected (PR review
is the real gate). These thresholds are a starting point, not a firm spec —
tune them here as real-world scraper behavior teaches you more.

## Running locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 run.py          # scrapes everything, updates prices.json in place
pytest tests/           # unit tests for the sanity/merge logic (pure functions,
                         # no network — the scrapers themselves aren't unit
                         # tested here, see "Testing philosophy" below)
```

## Adding a new vendor

1. Create `scrapers/<vendor>_scraper.py` exposing `scrape(last_verified: str) -> list[dict]`.
2. Fetch the vendor's real, current pricing page and inspect its actual
   structure before writing a parser — don't guess at markup from memory or
   from a summarized/reformatted view of the page (column order in
   particular is easy to get backwards). Match on a structural signature
   (e.g. a table's header row) rather than position, so unrelated page
   changes don't silently break the scraper.
3. Register it in `SCRAPERS` in `run.py`.
4. Test it against the live page before committing — a scraper that "looks
   right" and a scraper that's actually verified against real fetched output
   are not the same thing.

## Testing philosophy

`tests/` covers the deterministic logic (`sanity.py`'s thresholds, `run.py`'s
merge/failure-isolation behavior) with pure-function unit tests — no network
calls, no mocked HTML. The scrapers themselves are **not** unit-tested here:
each vendor's pricing page is real, external, and will change shape over time
in ways a saved-HTML-fixture test can't catch anyway. Correctness for the
scrapers comes from: (a) matching on structural signatures instead of brittle
fixed selectors, (b) raising loudly instead of guessing when a page doesn't
match what's expected, and (c) the weekly CI run + PR review being the actual
feedback loop that catches drift. This is a deliberate scope choice — see
the design discussion in the source project that spawned this repo (routed's
`docs/plan/specs/2026-07-03-model-pricing-tooling-design.md`, §5 and §6).
