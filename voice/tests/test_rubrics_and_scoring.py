"""Rubric loading/merging/hashing, and the scoring maths on top of it."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from runner.rubrics import Criterion, Rubric, apply_scale, load_rubric
from runner.scoring import (
    MEAN_GAP_DOOR,
    ScoredCell,
    paired_wtl,
    score_cell,
    sign_test_p,
    summarise,
    verdict,
)

CONFIGS = Path(__file__).resolve().parent.parent / "configs"


# --------------------------------------------------------------------------
# Rubrics
# --------------------------------------------------------------------------


def test_base_voice_rubric_matches_the_plan():
    rb = load_rubric(CONFIGS, "voice", "text_to_speech")
    weights = {c.key: c.weight for c in rb.criteria}
    assert weights == {
        "text_accuracy": 0.30,
        "pronunciation": 0.20,
        "naturalness": 0.20,
        "clarity": 0.15,
        "audio_quality": 0.15,
    }
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_who_scores_what_is_declared_not_assumed():
    rb = load_rubric(CONFIGS, "voice", "text_to_speech")
    assert rb.by_key("text_accuracy").scored_by == "measurement"
    assert rb.by_key("audio_quality").scored_by == "hybrid"
    # The judge must never be asked about word accuracy.
    assert "text_accuracy" not in {c.key for c in rb.judged_criteria}


def test_task_override_adds_style_and_still_sums_to_one():
    rb = load_rubric(CONFIGS, "voice", "styled_tts")
    keys = {c.key for c in rb.criteria}
    assert "style_adherence" in keys
    assert abs(sum(c.weight for c in rb.criteria) - 1.0) < 1e-9


def test_override_produces_a_different_hash_than_the_base():
    a = load_rubric(CONFIGS, "voice", "text_to_speech")
    b = load_rubric(CONFIGS, "voice", "styled_tts")
    assert a.rubric_hash != b.rubric_hash
    assert len(a.rubric_hash) == 64


def test_hash_is_stable_across_loads():
    assert (
        load_rubric(CONFIGS, "voice", "styled_tts").rubric_hash
        == load_rubric(CONFIGS, "voice", "styled_tts").rubric_hash
    )


def test_weights_that_do_not_sum_to_one_are_rejected_not_normalised(tmp_path):
    (tmp_path / "rubrics").mkdir()
    (tmp_path / "rubrics" / "voice.yaml").write_text(
        "modality: voice\ncriteria:\n"
        "  - {key: a, weight: 0.5, scored_by: judge}\n"
        "  - {key: b, weight: 0.2, scored_by: judge}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="weights sum to 0.700"):
        load_rubric(tmp_path, "voice", "text_to_speech")


def test_measurement_criterion_must_name_its_measurement(tmp_path):
    (tmp_path / "rubrics").mkdir()
    (tmp_path / "rubrics" / "voice.yaml").write_text(
        "modality: voice\ncriteria:\n  - {key: a, weight: 1.0, scored_by: measurement}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="names no `measurement`"):
        load_rubric(tmp_path, "voice", "text_to_speech")


def test_wer_scale_matches_the_plan_anchors():
    scale = load_rubric(CONFIGS, "voice", "text_to_speech").by_key("text_accuracy").scale
    assert apply_scale(scale, 0.0) == 10.0
    assert apply_scale(scale, 0.10) == pytest.approx(5.0)
    assert apply_scale(scale, 0.20) == 0.0
    assert apply_scale(scale, 0.50) == 0.0  # clamped, never negative
    assert apply_scale(scale, 0.05) == pytest.approx(7.5)


def test_mos_scale_maps_one_to_five_onto_zero_to_ten():
    scale = load_rubric(CONFIGS, "voice", "text_to_speech").by_key("audio_quality").scale
    assert apply_scale(scale, 1.0) == 0.0
    assert apply_scale(scale, 5.0) == 10.0
    assert apply_scale(scale, 3.0) == pytest.approx(5.0)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


@dataclass
class FakeScenario:
    id: str = "voi-001"
    task: str = "text_to_speech"
    text: str = "hello"


@dataclass
class FakeCheck:
    measurements: dict[str, Any] = field(default_factory=dict)
    passed: bool = True
    failed_gates: list[str] = field(default_factory=list)


class FakeJudge:
    def __init__(self, scores: dict[str, float] | None, status: str = "judged", error=None):
        self.status = status
        self.error = error
        self.criteria = (
            [type("JC", (), {"name": k, "score": v, "reasoning": ""})() for k, v in (scores or {}).items()]
            if scores
            else []
        )


RUBRIC = None


def _rubric():
    global RUBRIC
    if RUBRIC is None:
        RUBRIC = load_rubric(CONFIGS, "voice", "text_to_speech")
    return RUBRIC


JUDGE_ALL = {"pronunciation": 8.0, "naturalness": 7.0, "clarity": 9.0, "audio_quality": 6.0}


def test_weighted_score_combines_measured_and_judged():
    rb = _rubric()
    check = FakeCheck(measurements={"normalized_wer": 0.0, "audio_quality_1_5": 4.0})
    cell = score_cell(FakeScenario(), rb, "m1", check, FakeJudge(JUDGE_ALL), True)
    assert cell.status == "scored"
    # text_accuracy 10 (WER 0); audio_quality = 0.5*7.5 + 0.5*6.0 = 6.75
    assert cell.criterion_scores["text_accuracy"] == 10.0
    assert cell.criterion_scores["audio_quality"] == pytest.approx(6.75)
    expected = 0.30 * 10 + 0.20 * 8 + 0.20 * 7 + 0.15 * 9 + 0.15 * 6.75
    assert cell.score == pytest.approx(expected)


def test_gate_failure_is_an_earned_zero_and_the_judge_is_irrelevant():
    cell = score_cell(
        FakeScenario(), _rubric(), "m1", FakeCheck(passed=False, failed_gates=["wer_within_max"]), None, True
    )
    assert cell.status == "invalid"
    assert cell.score == 0.0
    assert "wer_within_max" in cell.note


def test_judge_failure_is_unjudged_never_zero():
    check = FakeCheck(measurements={"normalized_wer": 0.0, "audio_quality_1_5": 4.0})
    cell = score_cell(FakeScenario(), _rubric(), "m1", check, FakeJudge(None, "unjudged", "timeout"), True)
    assert cell.status == "unjudged"
    assert cell.score is None


def test_unmeasured_text_accuracy_redistributes_its_weight():
    """ASR failed. text_accuracy must not become 0 - its weight moves."""
    rb = _rubric()
    check = FakeCheck(measurements={"audio_quality_1_5": 4.0, "text_accuracy_unmeasured": True})
    cell = score_cell(FakeScenario(), rb, "m1", check, FakeJudge(JUDGE_ALL), True)
    assert cell.status == "scored"
    assert cell.unmeasured == ["text_accuracy"]
    assert "text_accuracy" not in cell.effective_weights
    assert sum(cell.effective_weights.values()) == pytest.approx(1.0)
    # Every surviving criterion is >= 6, so the composite must be too - a
    # zero-filled text_accuracy would have dragged it to ~5.
    assert cell.score > 6.0


def test_calibration_gate_marks_a_cell_untrusted_without_changing_its_number():
    rb = _rubric()
    check = FakeCheck(measurements={"normalized_wer": 0.0, "audio_quality_1_5": 4.0})
    trusted = score_cell(FakeScenario(), rb, "m1", check, FakeJudge(JUDGE_ALL), True)
    untrusted = score_cell(FakeScenario(), rb, "m1", check, FakeJudge(JUDGE_ALL), False)
    assert trusted.calibration_trusted is True
    assert untrusted.calibration_trusted is False
    assert trusted.score == untrusted.score


# --------------------------------------------------------------------------
# Two lenses
# --------------------------------------------------------------------------


def _cells(pairs):
    out = []
    for sid, mid, score in pairs:
        out.append(
            ScoredCell(sid, mid, "text_to_speech", "scored", "hash", score)
        )
    return out


def test_paired_comparison_uses_the_tie_band():
    """TIE_BAND moved 0.5 -> 0.05 on 2026-09-04, so the gap that ties moved
    with it. A 0.02 gap is a tie; 3.0 is a win."""
    cells = _cells([("s1", "a", 8.0), ("s1", "b", 7.98), ("s2", "a", 9.0), ("s2", "b", 6.0)])
    p = paired_wtl(cells, "a", "b", "text_to_speech")
    assert (p.wins, p.ties, p.losses) == (1, 1, 0)
    assert p.decided == 1


def test_paired_comparison_only_uses_shared_scenarios():
    cells = _cells([("s1", "a", 8.0), ("s1", "b", 6.0), ("s2", "a", 9.0)])
    assert paired_wtl(cells, "a", "b", "text_to_speech").compared == 1


def test_sign_test_is_withheld_below_ten_decided():
    assert sign_test_p(8, 9) is None
    assert sign_test_p(10, 10) == pytest.approx(2 / 1024)


def test_mean_gap_door_names_a_winner():
    cells = _cells(
        [(f"s{i}", "a", 8.5) for i in range(6)] + [(f"s{i}", "b", 7.0) for i in range(6)]
    )
    summaries = [summarise(cells, "a", "text_to_speech"), summarise(cells, "b", "text_to_speech")]
    v = verdict(summaries, cells, "text_to_speech", True)
    assert v.winner == "a"
    assert v.mean_gap >= MEAN_GAP_DOOR


def test_win_rate_door_names_a_winner_the_mean_would_have_missed():
    """Judge compression: a 0.3 mean gap, but a is ahead on 6 of 6 decided."""
    pairs = []
    for i in range(6):
        pairs.append((f"s{i}", "a", 7.9))
        pairs.append((f"s{i}", "b", 7.0))
    cells = _cells(pairs)
    summaries = [summarise(cells, "a", "text_to_speech"), summarise(cells, "b", "text_to_speech")]
    v = verdict(summaries, cells, "text_to_speech", True)
    assert v.winner == "a"
    assert v.paired.win_rate == 1.0


def test_a_close_run_is_a_declared_tie():
    """At the 0.05 band a "close run" is much closer than it was: a 0.2 gap
    over six scenarios now clears both doors and names a winner, so this
    exercises the band with a 0.02 gap."""
    pairs = []
    for i in range(6):
        pairs.append((f"s{i}", "a", 7.80))
        pairs.append((f"s{i}", "b", 7.78))
    cells = _cells(pairs)
    summaries = [summarise(cells, "a", "text_to_speech"), summarise(cells, "b", "text_to_speech")]
    v = verdict(summaries, cells, "text_to_speech", True)
    assert v.winner is None
    assert "tie" in v.reason


def test_a_winner_needs_coverage():
    cells = _cells([("s1", "a", 9.5)] + [(f"s{i}", "b", 7.0) for i in range(5)])
    cells += [
        ScoredCell(f"s{i}", "a", "text_to_speech", "unjudged", "hash", None) for i in range(1, 5)
    ]
    summaries = [summarise(cells, "a", "text_to_speech"), summarise(cells, "b", "text_to_speech")]
    v = verdict(summaries, cells, "text_to_speech", True)
    assert v.winner is None
    assert "coverage" in v.reason


def test_uncalibrated_winner_is_marked_provisional():
    cells = _cells([(f"s{i}", "a", 8.5) for i in range(6)] + [(f"s{i}", "b", 7.0) for i in range(6)])
    summaries = [summarise(cells, "a", "text_to_speech"), summarise(cells, "b", "text_to_speech")]
    v = verdict(summaries, cells, "text_to_speech", False)
    assert v.winner == "a"
    assert "PROVISIONAL" in v.reason


def test_unjudged_is_excluded_from_the_mean_while_invalid_counts_as_zero():
    cells = [
        ScoredCell("s1", "a", "text_to_speech", "scored", "h", 8.0),
        ScoredCell("s2", "a", "text_to_speech", "unjudged", "h", None),
        ScoredCell("s3", "a", "text_to_speech", "invalid", "h", 0.0),
    ]
    s = summarise(cells, "a", "text_to_speech")
    assert s.mean == pytest.approx(4.0)  # (8 + 0) / 2, the unjudged cell excluded
    assert s.judged == 2 and s.attempted == 3 and s.unjudged == 1 and s.invalid == 1
