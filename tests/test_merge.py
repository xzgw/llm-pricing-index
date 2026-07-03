from run import merge


def test_new_vendor_entries_are_added():
    existing = []
    fresh = {"z.ai": [{"model": "glm-4.7", "vendor": "z.ai", "input_price_usd": 0.6, "output_price_usd": 2.2}]}
    merged, changes = merge(existing, fresh)
    assert len(merged) == 1
    assert any("NEW z.ai/glm-4.7" in c for c in changes)


def test_price_change_is_detected_and_applied():
    existing = [{"model": "glm-4.7", "vendor": "z.ai", "input_price_usd": 0.5, "output_price_usd": 2.0}]
    fresh = {"z.ai": [{"model": "glm-4.7", "vendor": "z.ai", "input_price_usd": 0.6, "output_price_usd": 2.2}]}
    merged, changes = merge(existing, fresh)
    assert merged[0]["input_price_usd"] == 0.6
    assert any("CHANGED z.ai/glm-4.7" in c for c in changes)


def test_unchanged_vendor_is_left_untouched_when_its_scraper_did_not_run():
    """The core failure-isolation guarantee: a vendor absent from
    fresh_by_vendor (because its scraper failed) must survive merge()
    completely unchanged — this is what protects existing data when one
    vendor's scraper breaks.
    """
    existing = [
        {"model": "gpt-5.4", "vendor": "openai", "input_price_usd": 1.0, "output_price_usd": 3.0},
        {"model": "glm-4.7", "vendor": "z.ai", "input_price_usd": 0.6, "output_price_usd": 2.2},
    ]
    # Only z.ai succeeded this run; openai's scraper failed and is absent.
    fresh = {"z.ai": [{"model": "glm-4.7", "vendor": "z.ai", "input_price_usd": 0.6, "output_price_usd": 2.2}]}
    merged, changes = merge(existing, fresh)

    openai_entries = [e for e in merged if e["vendor"] == "openai"]
    assert openai_entries == [existing[0]]
    assert not any("openai" in c for c in changes)


def test_negative_price_is_skipped_and_old_value_kept():
    existing = [{"model": "glm-4.7", "vendor": "z.ai", "input_price_usd": 0.6, "output_price_usd": 2.2}]
    fresh = {"z.ai": [{"model": "glm-4.7", "vendor": "z.ai", "input_price_usd": -1.0, "output_price_usd": 2.2}]}
    merged, changes = merge(existing, fresh)
    assert merged == existing
    assert any("SKIPPED" in c for c in changes)


def test_no_changes_produces_no_change_lines():
    existing = [{"model": "glm-4.7", "vendor": "z.ai", "input_price_usd": 0.6, "output_price_usd": 2.2}]
    fresh = {"z.ai": [{"model": "glm-4.7", "vendor": "z.ai", "input_price_usd": 0.6, "output_price_usd": 2.2}]}
    merged, changes = merge(existing, fresh)
    assert merged == existing
    assert changes == []
