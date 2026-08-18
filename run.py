#!/usr/bin/env python3
"""Orchestrator: run every vendor scraper independently, merge fresh results
into prices.json, and print a human-readable change summary (used as the PR
body by the GitHub Actions workflow — see .github/workflows/update-prices.yml).

One vendor's scraper failing must never block the others, and must never
touch that vendor's existing entries in prices.json (they stay as last-known-
good until a future run succeeds) — see README.md "Failure isolation".
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

from scrapers import (
    alibaba_scraper,
    anthropic_scraper,
    deepseek_scraper,
    google_scraper,
    minimax_scraper,
    moonshot_scraper,
    openai_scraper,
    openrouter_scraper,
    zai_scraper,
)
from scrapers.sanity import check_entry

REPO_ROOT = Path(__file__).parent
PRICES_FILE = REPO_ROOT / "prices.json"

SCRAPERS = {
    "openai": openai_scraper.scrape,
    "anthropic": anthropic_scraper.scrape,
    "google": google_scraper.scrape,
    "deepseek": deepseek_scraper.scrape,
    "moonshot": moonshot_scraper.scrape,
    "minimax": minimax_scraper.scrape,
    "alibaba": alibaba_scraper.scrape,
    "z.ai": zai_scraper.scrape,
    # LAST ON PURPOSE. openrouter is gap-filling: it reads prices.json to learn
    # which models an official-vendor scraper already covers. dict order is
    # insertion order in Python 3.7+, and run_all_scrapers iterates in that
    # order — but note it reads the file, not this run's in-memory results, so
    # its gap set reflects the PREVIOUS run. That is deliberate and safe: a
    # duplicate can survive at most one cycle, and the alternative (threading
    # this run's results into a scraper) would break the per-vendor isolation
    # that keeps one vendor's failure from touching another's data.
    "openrouter": openrouter_scraper.scrape,
}


def load_prices() -> list[dict]:
    if not PRICES_FILE.exists():
        return []
    return json.loads(PRICES_FILE.read_text())


def run_all_scrapers(last_verified: str) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Returns (vendor -> fresh entries, vendor -> error message for failed vendors)."""
    results: dict[str, list[dict]] = {}
    failures: dict[str, str] = {}
    for vendor, scrape_fn in SCRAPERS.items():
        try:
            results[vendor] = scrape_fn(last_verified)
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            failures[vendor] = f"{type(exc).__name__}: {exc}"
    return results, failures


def merge(existing: list[dict], fresh_by_vendor: dict[str, list[dict]]) -> tuple[list[dict], list[str]]:
    """Replaces each successfully-scraped vendor's block of entries with the
    fresh data; leaves every other vendor's existing entries untouched.
    Returns (merged_list, change_summary_lines).
    """
    existing_by_key = {(e["vendor"], e["model"]): e for e in existing}
    changes: list[str] = []

    merged: list[dict] = [e for e in existing if e["vendor"] not in fresh_by_vendor]

    for vendor, fresh_entries in fresh_by_vendor.items():
        for entry in fresh_entries:
            key = (vendor, entry["model"])
            old = existing_by_key.get(key)

            problems = check_entry(entry, old)
            hard_fail = any("negative" in p or "missing" in p for p in problems)
            if hard_fail:
                changes.append(f"  SKIPPED {vendor}/{entry['model']}: {'; '.join(problems)}")
                if old is not None:
                    merged.append(old)  # keep last-known-good
                continue
            if problems:
                changes.append(f"  WARNING {vendor}/{entry['model']}: {'; '.join(problems)}")

            merged.append(entry)

            if old is None:
                changes.append(f"  NEW {vendor}/{entry['model']}: input=${entry['input_price_usd']} output=${entry['output_price_usd']}")
            elif (old["input_price_usd"], old["output_price_usd"]) != (entry["input_price_usd"], entry["output_price_usd"]):
                changes.append(
                    f"  CHANGED {vendor}/{entry['model']}: "
                    f"input ${old['input_price_usd']} -> ${entry['input_price_usd']}, "
                    f"output ${old['output_price_usd']} -> ${entry['output_price_usd']}"
                )

    merged.sort(key=lambda e: (e["vendor"], e["model"]))
    return merged, changes


def main() -> int:
    last_verified = datetime.date.today().isoformat()
    existing = load_prices()

    fresh_by_vendor, failures = run_all_scrapers(last_verified)
    merged, changes = merge(existing, fresh_by_vendor)

    PRICES_FILE.write_text(json.dumps(merged, indent=2) + "\n")

    print(f"=== llm-pricing-index update — {last_verified} ===\n")
    if changes:
        print("Changes:")
        for line in changes:
            print(line)
    else:
        print("No price changes detected.")

    if failures:
        print("\nVendors that FAILED this run (their existing entries were left untouched):")
        for vendor, msg in failures.items():
            print(f"  {vendor}: {msg}")

    # Signal to the GitHub Actions workflow whether anything changed, so it
    # only opens a PR when there's something to review.
    github_output = __import__("os").environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"has_changes={'true' if changes else 'false'}\n")

    return 1 if failures and not fresh_by_vendor else 0


if __name__ == "__main__":
    sys.exit(main())
