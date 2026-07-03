"""Sanity checks applied to freshly-scraped entries before they're allowed
into prices.json. Thresholds are deliberately conservative and simple —
exact tuning is left to this repo's own experience over time (per the
routed spec that spawned this repo: "具体门槛留给 llm-pricing-index 自己的
实施阶段定"). Every PR is human-reviewed regardless (§5.2: PR, never a
direct push to main) — this is a second line of defense that keeps an
obviously-wrong scrape (e.g. a mis-parsed column) from ever reaching a
diff at all, not the only line of defense.
"""

from __future__ import annotations

# A price jumping by more than this multiple (in either direction) versus
# the previous known value is far more likely to be a parsing bug (wrong
# column, wrong units) than a genuine vendor price change.
MAX_JUMP_RATIO = 10.0


def check_entry(new: dict, old: dict | None) -> list[str]:
    """Returns a list of problem descriptions (empty = clean). Does not
    raise — callers decide whether a problem means "drop this entry" or
    "keep it but flag loudly for human review" (run.py drops on negative,
    warns-but-keeps otherwise).
    """
    problems: list[str] = []

    for field in ("input_price_usd", "output_price_usd"):
        v = new.get(field)
        if v is None:
            problems.append(f"{field} is missing")
            continue
        if v < 0:
            problems.append(f"{field}={v} is negative")

    if old is not None:
        for field in ("input_price_usd", "output_price_usd"):
            old_v, new_v = old.get(field), new.get(field)
            if old_v in (None, 0) or new_v in (None, 0):
                continue
            ratio = max(new_v, old_v) / min(new_v, old_v)
            if ratio > MAX_JUMP_RATIO:
                problems.append(
                    f"{field} jumped {old_v} -> {new_v} ({ratio:.1f}x, exceeds "
                    f"{MAX_JUMP_RATIO}x threshold) — likely a scraper bug, not "
                    "a real price change"
                )

    return problems
