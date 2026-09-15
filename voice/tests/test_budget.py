"""Budget caps: the total, and one per provider.

A voice run bills more than one account — generation on the model's provider,
ASR on its own — so a single combined cap protects neither. It trips on the
sum, which means a cheap service eats the headroom an expensive one needed,
and a cap set to match a prepaid balance stops the run long before that
balance is actually gone.
"""

from __future__ import annotations

import pytest

from runner.generate import Budget, BudgetExceeded


def test_no_cap_never_stops_anything():
    b = Budget(None)
    b.add(999_000_000, "openai")
    b.guard("a call", "openai")          # must not raise


def test_the_total_cap_still_behaves_as_it_always_did():
    b = Budget(1.00)
    b.add(999_999)
    b.guard("still under")               # 0.999999 < 1.00
    b.add(2)
    with pytest.raises(BudgetExceeded, match=r"\$1\.00"):
        b.guard("now over")


def test_a_provider_cap_binds_that_provider_alone():
    """The separation is the whole point: one arm stopping must not stop
    the other, and an uncapped provider is never touched."""
    b = Budget(None, {"byteplus": 2.00})
    b.add(2_500_000, "byteplus")
    b.add(9_000_000, "google-vertex")

    with pytest.raises(BudgetExceeded, match="byteplus"):
        b.guard("the next Seedance clip", "byteplus")
    b.guard("the next Omni clip", "google-vertex")     # uncapped, must not raise
    b.guard("an unattributed call")                    # no provider, must not raise


def test_spend_is_tracked_per_provider_not_pooled():
    b = Budget(None, {"byteplus": 2.00})
    b.add(1_000_000, "byteplus")
    b.add(50_000_000, "google-vertex")
    assert b.spent_micro_for("byteplus") == 1_000_000
    assert b.spent_micro_for("google-vertex") == 50_000_000
    assert b.spent_micro == 51_000_000
    b.guard("still fine", "byteplus")     # google-vertex's spend must not bind it


def test_the_total_cap_and_a_provider_cap_are_both_enforced():
    """Whichever binds first stops the call — they are not alternatives."""
    b = Budget(1.00, {"byteplus": 10.00})
    b.add(1_500_000, "byteplus")          # under its own cap, over the total
    with pytest.raises(BudgetExceeded, match="budget cap"):
        b.guard("blocked by the total", "byteplus")


def test_malformed_provider_caps_are_refused_not_shrugged_off():
    from runner.cli import _parse_provider_caps
    assert _parse_provider_caps(["openai=80", "google-vertex=30.5"]) == {
        "openai": 80.0, "google-vertex": 30.5}
    assert _parse_provider_caps([]) == {}
    for bad in ("openai", "=80", "openai=eighty", "openai=0", "openai=-5"):
        with pytest.raises(SystemExit):
            _parse_provider_caps([bad])
    with pytest.raises(SystemExit, match="twice"):
        _parse_provider_caps(["openai=80", "openai=90"])
