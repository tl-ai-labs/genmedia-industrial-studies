import math

import pytest

from runner.matrix import build_matrix
from runner.scoring import (measured_criterion_score, pairwise_verdict,
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
    a = {f"s{i}": 7.8 for i in range(10)}
    b = {f"s{i}": 7.5 for i in range(10)}
    models = {"a": _model(7.8, a, cost=3.2), "b": _model(7.5, b, cost=0.8)}
    v = pairwise_verdict("t", "a", "b", models)
    assert v["winner"] is None
    assert "tie on quality" in v["note"]
    assert "cheaper: b" in v["note"]


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
