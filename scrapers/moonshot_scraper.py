"""Moonshot AI (Kimi) pricing scraper.

Source: https://platform.kimi.ai/docs/pricing/chat (Mintlify-hosted docs —
the platform rebranded from `platform.moonshot.ai`/`platform.moonshot.cn`
to `platform.kimi.ai`/`platform.kimi.com` some time before this was
written; both old hosts 301-redirect to the new ones, confirmed live).
`kimi.ai` is the **international, USD-billed** platform — `kimi.com` is the
mainland-China, RMB-billed twin of the same docs (same paths, same
Mintlify template, different currency). We deliberately use `kimi.ai`/USD,
not `kimi.com`/RMB, per this repo's schema (USD only, no currency-
conversion allowed — see README).

This site's rendered HTML has NO server-side `<table>` markup at all — page
content hydrates client-side from a Next.js RSC payload. But Mintlify
serves a markdown "source of truth" for every doc page at `<path>.md`
(verified live: fetch a page and `.md` returns 200 with real content — this
is a documented Mintlify feature, not a scraping trick). That `.md` source
is NOT plain GFM markdown for tables — Kimi's docs use an MDX
`<DocTable columns={[...]} rows={[...]} />` component, i.e. the "table" is
literally a JS array/object literal embedded in the page source. We parse
that literal with regexes (not a JS/JSON parser — it isn't valid JSON:
unquoted keys, JSX price cells like `<>{"$"}0.30</>`), matching two
independent structural signals rather than a fixed position: (1) the
`columns=[...]` block's `title:` strings give us the schema for that page
(model-family pages have 6 columns incl. a Cache-Hit/Cache-Miss split;
the legacy Moonshot V1 page has only 5, no cache columns — handled
generically by header-title matching, not a hardcoded column count), and
(2) every cell in `rows=[...]` is one of exactly two literal shapes —
`"quoted string"` or `<>{"$"}NUMBER</>` — so we scan the whole rows block
for all occurrences of either shape, in document order, and chunk them into
groups of `len(columns)`; this reads correctly regardless of how a
particular row's JSX is whitespace-formatted, and doesn't need to parse
brackets/commas by hand (context-window cell text like "1,048,576 tokens"
has a comma *inside* a quoted string, which would break naive comma-
splitting — this approach never comma-splits at all).

Column-name -> schema mapping (matched by case-insensitive prefix, not
position): "Input Price (Cache Hit)" -> cache_read_price_usd, "Input Price
(Cache Miss)" -> input_price_usd (the real always-charged rate with no
cache in play — same meaning as every other vendor's plain "Input"
column), "Output Price" -> output_price_usd, bare "Input Price" (Moonshot
V1 page, no cache columns at all) -> input_price_usd directly. No vendor
page here publishes a distinct cache-*write* rate (Kimi's context caching
is automatic, no separate create/write step) — cache_write_price_usd is
always None.

Which model-family pages to scrape is discovered dynamically rather than
hardcoded: the pricing overview page's rendered HTML (not its `.md`, which
only lists the current "featured" cards) links every model-family pricing
page at a href matching `^/docs/pricing/chat(-.+)?$` — this prefix is
Kimi's own routing convention for "chat/completion model pricing" and
structurally excludes the sibling `/docs/pricing/batch`, `/docs/pricing/
tools`, and `/docs/pricing/limits` pages (batch-discount multiplier, tool-
call pricing, and rate limits respectively — none of them a per-token
model price) without needing a name-based exclude-list. This also means a
newly added or removed model family is picked up automatically. As of this
writing that resolves to: chat-k3 (Kimi K3), chat-k26 (K2.6), chat-k27-code
(K2.7 Code), chat-k25 (K2.5 — still live and priced, just no longer
featured on the overview's card grid), and chat-v1 (Moonshot V1 — legacy,
each row's price is unaffected by the "full platform sunset expected on
August 31" advisory text on that page, which is a future date; re-verify
this page's continued existence after that date passes).
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import PriceEntry, fetch

SOURCE_URL = "https://platform.kimi.ai/docs/pricing/chat"
VENDOR = "moonshot"
_PRICING_PAGE_HREF_RE = re.compile(r"^/docs/pricing/chat(-.+)?$")

_COLUMNS_RE = re.compile(r"columns=\{\[(.*?)\]\}\s*rows=", re.S)
_ROWS_RE = re.compile(r"rows=\{\[(.*?)\]\}\s*/>", re.S)
_TITLE_RE = re.compile(r'title:\s*"([^"]+)"')
_CELL_TOKEN_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|<>\{"\$"\}([\d.]+)</>')


def _discover_pricing_pages(overview_html: str) -> list[str]:
    soup = BeautifulSoup(overview_html, "html.parser")
    hrefs: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href == "/docs/pricing/chat":
            continue  # the overview page itself
        if _PRICING_PAGE_HREF_RE.match(href) and href not in hrefs:
            hrefs.append(href)
    return hrefs


def _parse_doc_table(md: str) -> tuple[list[str], list[list[tuple[str, str | float]]]] | None:
    cols_match = _COLUMNS_RE.search(md)
    rows_match = _ROWS_RE.search(md)
    if not cols_match or not rows_match:
        return None

    columns = _TITLE_RE.findall(cols_match.group(1))
    if not columns:
        return None

    tokens: list[tuple[str, str | float]] = []
    for m in _CELL_TOKEN_RE.finditer(rows_match.group(1)):
        if m.group(1) is not None:
            tokens.append(("str", m.group(1)))
        else:
            tokens.append(("price", float(m.group(2))))

    if len(tokens) % len(columns) != 0:
        return None  # malformed/unexpected shape — caller treats as a miss

    rows = [tokens[i : i + len(columns)] for i in range(0, len(tokens), len(columns))]
    return columns, rows


def _col_index(columns: list[str], predicate) -> int | None:
    for i, title in enumerate(columns):
        if predicate(title.lower()):
            return i
    return None


def scrape(last_verified: str) -> list[dict]:
    overview_html = fetch(SOURCE_URL)
    page_hrefs = _discover_pricing_pages(overview_html)
    if not page_hrefs:
        raise RuntimeError(
            "moonshot_scraper: found zero links matching /docs/pricing/chat* "
            "on the pricing overview page — the page structure likely "
            "changed; needs a manual look, not a silent empty result."
        )

    entries: list[dict] = []
    pages_parsed = 0
    for href in page_hrefs:
        page_url = urljoin(SOURCE_URL, href) + ".md"
        md = fetch(page_url)
        parsed = _parse_doc_table(md)
        if parsed is None:
            continue
        columns, rows = parsed
        pages_parsed += 1

        model_idx = _col_index(columns, lambda t: t.startswith("model"))
        cache_hit_idx = _col_index(columns, lambda t: t.startswith("input price") and "cache hit" in t)
        cache_miss_idx = _col_index(columns, lambda t: t.startswith("input price") and "cache miss" in t)
        plain_input_idx = _col_index(columns, lambda t: t.strip() == "input price")
        output_idx = _col_index(columns, lambda t: t.startswith("output price"))

        input_idx = cache_miss_idx if cache_miss_idx is not None else plain_input_idx
        if model_idx is None or input_idx is None or output_idx is None:
            continue  # this page's table doesn't have the shape we need

        for row in rows:
            model_cell = row[model_idx]
            if model_cell[0] != "str":
                continue
            model_id = model_cell[1].strip().lower()
            if not model_id:
                continue

            input_price = row[input_idx][1] if row[input_idx][0] == "price" else None
            output_price = row[output_idx][1] if row[output_idx][0] == "price" else None
            if input_price is None or output_price is None:
                continue

            cache_read = None
            if cache_hit_idx is not None and row[cache_hit_idx][0] == "price":
                cache_read = row[cache_hit_idx][1]

            entries.append(
                PriceEntry(
                    model=model_id,
                    vendor=VENDOR,
                    input_price_usd=input_price,
                    output_price_usd=output_price,
                    cache_read_price_usd=cache_read,
                    cache_write_price_usd=None,  # No vendor page publishes a cache-write rate.
                    source_url=page_url.removesuffix(".md"),
                    last_verified=last_verified,
                ).to_dict()
            )

    if pages_parsed == 0:
        raise RuntimeError(
            "moonshot_scraper: found pricing sub-pages but none of their "
            "`.md` sources matched the expected DocTable columns=[...] / "
            "rows=[...] shape — the page structure likely changed."
        )
    if not entries:
        raise RuntimeError("moonshot_scraper: parsed pricing tables but extracted zero usable entries.")

    return entries
