import math

import pytest

from runner.matrix import build_matrix
from runner.scoring import (is_tie, measured_criterion_score, pairwise_verdict,
                            sign_test_p, technical_compliance_score)


# ---- technical_compliance: the video measured criterion -------------------

FULL = {"duration_s": 8.0, "min_duration_s": 7.0, "max_duration_s": 9.0,
        "width": 1920, "height": 1080, "target_width": 1920, "target_height": 1080}


def test_full_spec_scores_ten():
    assert technical_compliance_score(FULL) == 10.0


def test_resolution_shortfall_linear():
    m = dict(FULL, width=1280, height=720)
    assert technical_compliance_score(m) == pytest.approx(10.0 * 1280 / 1920, abs=1e-3)


def test_duration_violation_linear():
    m = dict(FULL, duration_s=5.6)          # 20% under the 7s floor -> 1 - 0.4
    assert technical_compliance_score(m) == pytest.approx(6.0, abs=0.01)
    m = dict(FULL, duration_s=3.5)          # 50% under -> 0
    assert technical_compliance_score(m) == 0.0
    m = dict(FULL, duration_s=10.8)         # 20% over the 9s ceiling
    assert technical_compliance_score(m) == pytest.approx(6.0, abs=0.01)


def test_worst_component_wins():
    m = dict(FULL, width=1280, height=720, duration_s=5.6)
    assert technical_compliance_score(m) == pytest.approx(6.0, abs=0.01)


def test_oversized_delivery_is_not_a_bonus():
    m = dict(FULL, width=3840, height=2160)
    assert technical_compliance_score(m) == 10.0


def test_missing_measures_is_none_not_zero():
    assert technical_compliance_score({}) is None
    assert technical_compliance_score({"duration_s": 8.0}) is None  # no bounds
    assert measured_criterion_score("technical_compliance", {}) is None


def test_dispatch():
    assert measured_criterion_score("technical_compliance", FULL) == 10.0
    assert measured_criterion_score("no_such_criterion", FULL) is None


# ---- verdict machinery (modality-agnostic, kept under test here too) ------

def test_sign_test_matches_plan_example():
    p = sign_test_p(17, 20)
    assert math.isclose(p, 1351 / 2 ** 20, rel_tol=1e-9)
    assert 0.001 < p < 0.002


def _model(mean, by_scenario, coverage=1.0, invalid=0, success=1.0,
           cost=3.2, p50=90_000):
    return {"mean": mean, "by_scenario": by_scenario, "coverage": coverage,
            "invalid": invalid, "success_rate": success,
            "gen_cost_per_scenario_usd": cost, "judge_cost_per_scenario_usd": 0.01,
            "latency_p50_ms": p50, "latency_max_ms": p50 * 2}


def test_verdict_mean_gap_door():
    models = {"a": _model(8.1, {f"s{i}": 8.1 for i in range(10)}),
              "b": _model(7.5, {f"s{i}": 7.5 for i in range(10)})}
    v = pairwise_verdict("t", "a", "b", models)
    assert v["winner"] == "a" and "mean gap" in v["door"]


def test_verdict_tie_broken_only_by_facts():
    # a split 5-5 on scenarios and level on the mean: no door opens
    a = {f"s{i}": (7.8 if i % 2 else 7.5) for i in range(10)}
    b = {f"s{i}": (7.5 if i % 2 else 7.8) for i in range(10)}
    models = {"a": _model(7.65, a, cost=3.2), "b": _model(7.65, b, cost=0.8)}
    v = pairwise_verdict("t", "a", "b", models)
    assert v["winner"] is None
    assert "tie on quality" in v["note"]
    assert "cheaper: b" in v["note"]


def test_only_identical_scenario_scores_tie():
    """Study lead, 2026-09-14: 100% vs 100% is a tie; 99% vs 100% is a win for
    the 100. There is no tie band — float noise from the weighted sum is the
    only thing absorbed."""
    assert is_tie(0.0)
    assert is_tie((0.1 + 0.2) - 0.3)                              # float noise
    assert not is_tie(10.0 - 9.9)                                 # 100% vs 99%
    assert not is_tie(10.0 - 9.7)                                 # was a "tie" at 0.5
    models = {"a": _model(9.9, {"s0": 10.0, "s1": 9.9, "s2": 8.0}),
              "b": _model(9.2, {"s0": 10.0, "s1": 10.0, "s2": 7.9})}
    v = pairwise_verdict("t", "a", "b", models)
    assert (v["wins_a"], v["ties"], v["wins_b"]) == (1, 1, 1)


def test_verdict_coverage_floor_blocks_winner():
    models = {"a": _model(9.0, {f"s{i}": 9.0 for i in range(5)}, coverage=0.5),
              "b": _model(7.0, {f"s{i}": 7.0 for i in range(5)}, coverage=1.0)}
    v = pairwise_verdict("t", "a", "b", models)
    assert v["winner"] is None
    assert "coverage" in v["note"]


def test_matrix_filters_by_task_support():
    from runner.loaders import Scenario

    class M:
        def __init__(self, id, supports):
            self.id, self.supports, self.modality = id, supports, "video"

    s = Scenario(id="s1", modality="video", task="text_to_video",
                 prompt="p", expected="e")
    cells = build_matrix([s], [M("does-video", ["text_to_video"]),
                               M("cannot", ["image_to_video"])])
    states = {(c.scenario_id, c.model_id): c.state for c in cells}
    assert states[("s1", "does-video")] == "planned"
    assert states[("s1", "cannot")] == "skipped"   # never attempted, n/a


def test_a_blocked_door_is_not_restated_as_a_small_gap():
    """A 2-point gap blocked by the coverage floor once printed as
    '... no winner declared | tie on quality (mean gap 2.00 < 0.5 ...)' —
    the second clause contradicting the first."""
    models = {"a": _model(9.0, {f"s{i}": 9.0 for i in range(5)}, coverage=0.5),
              "b": _model(7.0, {f"s{i}": 7.0 for i in range(5)}, coverage=1.0)}
    v = pairwise_verdict("t", "a", "b", models)
    assert "coverage" in v["note"]
    assert "< 0.5" not in v["note"] and "tie on quality" not in v["note"]


def test_the_tally_accounts_for_every_scenario():
    a = _model(8.0, {"s1": 8.0, "s2": 8.0})
    b = _model(8.0, {"s1": 8.0, "s3": 8.0})
    a["no_result"] = {"s3": "failed", "s4": "unjudged"}
    b["no_result"] = {"s2": "failed", "s4": "failed"}
    v = pairwise_verdict("t", "a", "b", {"a": a, "b": b},
                         {"s1", "s2", "s3", "s4"})
    assert v["n_scenarios"] == 4 and v["n_common"] == 1 and v["not_compared"] == 3
    assert v["missed"] == {"both": ["s4"], "a": ["s3"], "b": ["s2"]}
    # an unjudged cell is not a model failure, so the word "failed" is withheld
    assert v["missed_all_failed"] is False
