from runner.cost import compute_cost, estimate_call_micro_usd
from runner.loaders import Price


PER_IMAGE = Price(unit="per_image", usd=0.067, tier="1K", source="t", as_of="2026-08-31")
PER_TOKEN = Price(unit="per_token", usd_in_per_1m=10.0, usd_out_per_1m=40.0,
                  est_usd_per_call=0.07, source="t", as_of="2026-08-31")


def test_per_image_api_reported():
    c = compute_cost(PER_IMAGE, {"images": 1})
    assert c == {"micro_usd": 67000, "basis": "per_image@1K",
                 "price_as_of": "2026-08-31", "usage_source": "api_reported"}
    assert isinstance(c["micro_usd"], int)


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


def test_single_rate_output_tokens():
    p = Price(unit="per_token", usd_per_1m=40.0, source="t", as_of="2026-08-31")
    c = compute_cost(p, {"output_tokens": 1_000_000})
    assert c["micro_usd"] == 40_000_000  # $40 in micro-USD


def test_preflight_estimates():
    assert estimate_call_micro_usd(PER_IMAGE) == 67000
    assert estimate_call_micro_usd(PER_TOKEN) == 70000
