"""Usage x price table -> integer micro-USD (plan §10, §15).

Rules:
  * Cost is derived from returned usage fields wherever the provider reports
    them (`usage_source: api_reported`); if usage is missing, we fall back to
    the price entry's `est_usd_per_call` and label it `estimated` — the label
    follows the number into the report.
  * Integer micro-USD everywhere ($1 = 1,000,000). Floats accumulate rounding
    error across 100 scenarios.
  * `est_usd_per_call` is also what the pre-flight budget uses for token-priced
    models. It never overrides reported usage.
"""
from __future__ import annotations

MICRO = 1_000_000


def _micro(usd: float) -> int:
    return round(usd * MICRO)


def compute_cost(price, usage: dict) -> dict:
    """price: loaders.Price. usage: raw provider counters, verbatim.

    Returns {"micro_usd", "basis", "price_as_of", "usage_source"}.
    """
    unit = price.unit

    if unit == "per_image":
        images = usage.get("images")
        source = "api_reported" if images else "estimated"
        n = images if images else 1
        basis = f"per_image@{price.tier}" if price.tier else "per_image"
        return _row(_micro(price.usd) * n, basis, price, source)

    if unit == "per_token":
        tokens_in = _first(usage, "input_tokens", "prompt_token_count")
        tokens_out = _first(usage, "output_tokens", "candidates_token_count")
        if tokens_in is None and tokens_out is None:
            if price.est_usd_per_call is not None:
                return _row(_micro(price.est_usd_per_call), "est_per_call", price, "estimated")
            return _row(0, "per_token(no_usage)", price, "estimated")
        micro = 0
        if tokens_in and price.usd_in_per_1m:
            micro += round(tokens_in * price.usd_in_per_1m * MICRO / 1_000_000)
        out_rate = price.usd_out_per_1m or price.usd_per_1m
        if tokens_out and out_rate:
            micro += round(tokens_out * out_rate * MICRO / 1_000_000)
        return _row(micro, "per_token", price, "api_reported")

    if unit == "per_1k_chars":
        chars = _first(usage, "characters", "character_count")
        if chars is None:
            return _row(0, "per_1k_chars(no_usage)", price, "estimated")
        return _row(round(chars * price.usd * MICRO / 1000), "per_1k_chars", price, "api_reported")

    if unit == "per_minute":
        seconds = _first(usage, "seconds", "duration_s")
        if seconds is None:
            return _row(0, "per_minute(no_usage)", price, "estimated")
        return _row(round(seconds / 60 * price.usd * MICRO), "per_minute", price, "api_reported")

    raise ValueError(f"unknown price unit {unit!r}")


def estimate_call_micro_usd(price) -> int:
    """Pre-flight estimate for ONE call — printing and --budget only."""
    if price.unit == "per_image":
        return _micro(price.usd)
    if price.est_usd_per_call is not None:
        return _micro(price.est_usd_per_call)
    return 0


def _row(micro_usd: int, basis: str, price, usage_source: str) -> dict:
    return {"micro_usd": int(micro_usd), "basis": basis,
            "price_as_of": price.as_of, "usage_source": usage_source}


def _first(usage: dict, *keys):
    for k in keys:
        v = usage.get(k)
        if v is not None:
            return v
    return None
