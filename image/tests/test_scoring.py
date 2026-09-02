import math

from runner.matrix import build_matrix
from runner.scoring import (pairwise_verdict, sign_test_p,
                            text_accuracy_from_ocr, wer_to_score)


def test_text_accuracy_mapping():
    assert text_accuracy_from_ocr(1.0) == 10.0
    assert text_accuracy_from_ocr(0.97) > 9.0
    assert text_accuracy_from_ocr(0.8) == 5.0
    assert text_accuracy_from_ocr(0.6) == 0.0
    assert text_accuracy_from_ocr(0.2) == 0.0


def test_wer_mapping():
    assert wer_to_score(0.0) == 10.0
    assert wer_to_score(0.10) == 5.0
    assert wer_to_score(0.20) == 0.0
    assert wer_to_score(0.5) == 0.0


def test_sign_test_matches_plan_example():
    # 17 wins of 20 decided -> p ~ 0.001 (plan §16 verdict example)
    p = sign_test_p(17, 20)
    assert math.isclose(p, 1351 / 2 ** 20, rel_tol=1e-9)
    assert 0.001 < p < 0.002


def _model(mean, by_scenario, coverage=1.0, invalid=0, success=1.0,
           cost=0.07, p50=4000):
    return {"mean": mean, "by_scenario": by_scenario, "coverage": coverage,
            "invalid": invalid, "success_rate": success,
            "gen_cost_per_scenario_usd": cost, "judge_cost_per_scenario_usd": 0.003,
            "latency_p50_ms": p50, "latency_max_ms": p50 * 2}


def test_verdict_mean_gap_door():
    models = {"a": _model(8.1, {f"s{i}": 8.1 for i in range(10)}),
              "b": _model(7.5, {f"s{i}": 7.5 for i in range(10)})}
    v = pairwise_verdict("t", "a", "b", models)
    assert v["winner"] == "a" and "mean gap" in v["door"]


def test_verdict_win_rate_door_survives_compressed_means():
    # mean gap 0.4 (under the door) but a wins 8/10 decided by >0.5 each
    a_scores = {f"s{i}": (8.0 if i < 8 else 6.0) for i in range(10)}
    b_scores = {f"s{i}": (7.2 if i < 8 else 8.4) for i in range(10)}
    mean_a = sum(a_scores.values()) / 10
    mean_b = sum(b_scores.values()) / 10
    assert mean_a - mean_b < 0.5
    models = {"a": _model(round(mean_a, 2), a_scores),
              "b": _model(round(mean_b, 2), b_scores)}
    v = pairwise_verdict("t", "a", "b", models)
    assert v["winner"] == "a"
    assert "decided" in v["door"]
    assert v["sign_test_p"] is not None  # n >= 10 decided


def test_verdict_tie_inside_both_doors():
    a = {f"s{i}": 7.8 for i in range(10)}
    b = {f"s{i}": 7.5 for i in range(10)}
    models = {"a": _model(7.8, a, cost=0.07), "b": _model(7.5, b, cost=0.03)}
    v = pairwise_verdict("t", "a", "b", models)
    assert v["winner"] is None
    assert "tie on quality" in v["note"]
    assert "cheaper: b" in v["note"]  # broken only by facts


def test_verdict_coverage_floor_blocks_winner():
    models = {"a": _model(9.0, {f"s{i}": 9.0 for i in range(5)}, coverage=0.5),
              "b": _model(7.0, {f"s{i}": 7.0 for i in range(5)}, coverage=1.0)}
    v = pairwise_verdict("t", "a", "b", models)
    assert v["winner"] is None
    assert "coverage" in v["note"]


def test_sign_test_not_quoted_below_10_decided():
    a = {f"s{i}": 9.0 for i in range(5)}
    b = {f"s{i}": 7.0 for i in range(5)}
    models = {"a": _model(9.0, a), "b": _model(7.0, b)}
    v = pairwise_verdict("t", "a", "b", models)
    assert v["winner"] == "a"
    assert v["sign_test_p"] is None


def test_matrix_filters_by_task_support():
    from runner.loaders import Scenario

    class M:
        def __init__(self, id, supports):
            self.id, self.supports, self.modality = id, supports, "image"

    s_gen = Scenario(id="s1", modality="image", task="text_to_image",
                     prompt="p", expected="e")
    s_edit = Scenario(id="s2", modality="image", task="image_edit",
                      prompt="p", expected="e",
                      inputs={"source": "assets/x.png"})
    cells = build_matrix([s_gen, s_edit],
                         [M("both", ["text_to_image", "image_edit"]),
                          M("gen-only", ["text_to_image"])])
    states = {(c.scenario_id, c.model_id): c.state for c in cells}
    assert states[("s1", "gen-only")] == "planned"
    assert states[("s2", "gen-only")] == "skipped"   # never attempted, n/a
    assert states[("s2", "both")] == "planned"
