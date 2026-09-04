from runner.cost import compute_cost, estimate_call_micro_usd
from runner.loaders import Price

PER_SECOND = Price(unit="per_second", usd=0.40, est_usd_per_call=3.20,
                   source="t", as_of="2026-09-02")
PER_SECOND_BARE = Price(unit="per_second", usd=0.10, source="t", as_of="2026-09-02")
PER_TOKEN = Price(unit="per_token", usd_in_per_1m=10.0, usd_out_per_1m=40.0,
                  est_usd_per_call=0.07, source="t", as_of="2026-08-31")


def test_per_second_api_reported():
    c = compute_cost(PER_SECOND, {"seconds": 8.0})
    assert c == {"micro_usd": 3_200_000, "basis": "per_second",
                 "price_as_of": "2026-09-02", "usage_source": "api_reported"}
    assert isinstance(c["micro_usd"], int)


def test_per_second_requested_seconds_are_labelled_estimated():
    # the video API reports nothing back; the adapter records the REQUESTED
    # seconds and the estimate label must follow the number into the report
    c = compute_cost(PER_SECOND, {"seconds": 8, "seconds_source": "requested"})
    assert c["micro_usd"] == 3_200_000
    assert c["usage_source"] == "estimated"


def test_per_second_missing_usage_falls_back_to_estimate():
    c = compute_cost(PER_SECOND, {})
    assert c["micro_usd"] == 3_200_000
    assert c["basis"] == "est_per_call" and c["usage_source"] == "estimated"


def test_per_second_missing_usage_without_estimate_is_zero_and_labelled():
    c = compute_cost(PER_SECOND_BARE, {})
    assert c["micro_usd"] == 0
    assert c["basis"] == "per_second(no_usage)" and c["usage_source"] == "estimated"


def test_per_second_fractional_seconds():
    c = compute_cost(PER_SECOND_BARE, {"seconds": 7.5})
    assert c["micro_usd"] == 750_000


def test_per_token_both_sides():
    c = compute_cost(PER_TOKEN, {"input_tokens": 1000, "output_tokens": 2000})
    assert c["micro_usd"] == 10000 + 80000
    assert c["usage_source"] == "api_reported"


def test_per_token_gemini_field_names():
    c = compute_cost(PER_TOKEN, {"prompt_token_count": 1000, "candidates_token_count": 500})
    assert c["micro_usd"] == 10000 + 20000


def test_missing_usage_falls_back_to_estimate_and_is_labelled():
    c = compute_cost(PER_TOKEN, {})
    assert c["micro_usd"] == 70000
    assert c["usage_source"] == "estimated"
    assert c["basis"] == "est_per_call"


def test_preflight_estimates():
    assert estimate_call_micro_usd(PER_SECOND) == 3_200_000
    assert estimate_call_micro_usd(PER_TOKEN) == 70000
    # a per_second price with no per-call figure still estimates one second
    # rather than a silent zero
    assert estimate_call_micro_usd(PER_SECOND_BARE) == 100_000
