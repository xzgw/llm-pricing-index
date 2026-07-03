from scrapers.sanity import check_entry


def test_clean_entry_no_previous_value_has_no_problems():
    new = {"input_price_usd": 0.6, "output_price_usd": 2.2}
    assert check_entry(new, None) == []


def test_negative_price_is_flagged():
    new = {"input_price_usd": -0.6, "output_price_usd": 2.2}
    problems = check_entry(new, None)
    assert any("negative" in p for p in problems)


def test_missing_price_is_flagged():
    new = {"input_price_usd": None, "output_price_usd": 2.2}
    problems = check_entry(new, None)
    assert any("missing" in p for p in problems)


def test_small_price_change_is_not_flagged():
    old = {"input_price_usd": 0.6, "output_price_usd": 2.2}
    new = {"input_price_usd": 0.65, "output_price_usd": 2.3}
    assert check_entry(new, old) == []


def test_huge_jump_is_flagged():
    old = {"input_price_usd": 0.6, "output_price_usd": 2.2}
    new = {"input_price_usd": 60.0, "output_price_usd": 2.2}  # 100x
    problems = check_entry(new, old)
    assert any("jumped" in p for p in problems)


def test_zero_to_free_is_not_treated_as_a_jump():
    # A model going from a real price to genuinely free (0) shouldn't divide
    # by zero or false-positive as a "huge jump" — z.ai has real free-tier
    # models, this is a legitimate case.
    old = {"input_price_usd": 0.6, "output_price_usd": 2.2}
    new = {"input_price_usd": 0.0, "output_price_usd": 0.0}
    assert check_entry(new, old) == []
