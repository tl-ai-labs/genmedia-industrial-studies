"""
Scoring - weights to a scenario score, and two lenses to a verdict
(plan v1.2 section 14).

    criterion_score in 0..10        judge, or derived from a measurement
    scenario_score = sum(weight_i x criterion_score_i)      weights sum to 1
    model_score    = mean(scenario_score) over JUDGED scenarios only

    win / tie / loss = compare two models ON THE SAME scenario; tie at |d| <= 0.05

    A beats B  <=>  mean(A) - mean(B) >= 0.05
                OR  A wins >= 70% of the DECIDED scenarios
                backed by a sign test when there are >= 10 decided

WHY THE MEAN ALONE IS NOT ALLOWED TO DECIDE. LLM judges compress: almost
everything lands between 6.5 and 8.5, so two genuinely different models can
average 7.8 and 7.5 and look like a permanent tie. The win count is paired -
both models answered the identical scenario - so it survives the compression
that shrinks the mean gap. Neither lens is trusted alone.

THREE STATES, NOT TWO. A cell is scored, invalid (it produced something and
it was unusable - a real, earned 0), or unjudged (we have no measurement - a
dash, excluded from the mean, counted separately). Nothing missing ever
becomes a number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .rubrics import Rubric, apply_scale

# THE DECISION BAND, and the one place it is written down.
#
# Set to 0.05 on 2026-09-04 at the study owner's instruction (was 0.5).
# `runner.dashboard` imports MEAN_GAP_DOOR rather than carrying its own
# number, so the report and the board can never disagree about what counts
# as a difference.
#
# WHAT THE BAND NO LONGER DOES, recorded because it is load-bearing for
# anyone reading a verdict later. 0.5 was a rule of thumb picked before any
# run; the board replaced it with each model's MEASURED run-to-run spread,
# on the principle that a gap means nothing until it beats the variation a
# model shows against itself. At 0.05 that guard is gone: on the bank as it
# stands, five of the seven scenarios this band decides have a gap SMALLER
# than the noise floor measured on the same scenario - vr-ecom-01 (0.181 vs
# ±0.188), vr-ecom-02 (0.116 vs ±0.162), vr-game-04 (0.077 vs ±0.144),
# vr-ads-05 (0.707 vs ±2.228) and vr-game-01 (0.523 vs ±0.601). Those
# verdicts can invert on a re-run without either model changing.
#
# The floor is still measured and still printed beside every gap, so a
# reader can see the distance between what was decided and what was shown.
TIE_BAND = 0.05
WIN_RATE_DOOR = 0.70
MEAN_GAP_DOOR = 0.05
COVERAGE_DOOR = 0.80
SIGN_TEST_MIN_N = 10


@dataclass
class ScoredCell:
    scenario_id: str
    model_id: str
    task: str
    # "scored" | "invalid" | "unjudged" | "failed" | "skipped"
    status: str
    rubric_hash: str
    score: float | None = None
    criterion_scores: dict[str, float] = field(default_factory=dict)
    effective_weights: dict[str, float] = field(default_factory=dict)
    unmeasured: list[str] = field(default_factory=list)
    calibration_trusted: bool = True
    note: str = ""

    @property
    def as_record(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "model_id": self.model_id,
            "task": self.task,
            "status": self.status,
            "score": None if self.score is None else round(self.score, 4),
            "rubric_hash": self.rubric_hash,
            "criterion_scores": {k: round(v, 4) for k, v in self.criterion_scores.items()},
            "effective_weights": {k: round(v, 6) for k, v in self.effective_weights.items()},
            "unmeasured_criteria": self.unmeasured,
            "calibration_trusted": self.calibration_trusted,
            "note": self.note,
        }


def _redistribute(weights: dict[str, float], drop: set[str]) -> dict[str, float]:
    """
    Drop unmeasurable criteria and spread their weight over the survivors in
    proportion to their existing weights. Used when ASR failed and
    text_accuracy has no value: its weight moves to the criteria we DO have,
    rather than the criterion scoring 0 and dragging the cell down for a
    failure the model did not cause (plan section 18).
    """
    kept = {k: w for k, w in weights.items() if k not in drop}
    total = sum(kept.values())
    if total <= 0:
        return {}
    return {k: w / total for k, w in kept.items()}


def score_cell(
    scenario,
    rubric: Rubric,
    model_id: str,
    check_report,
    judge_record,
    calibration_passed: bool,
) -> ScoredCell:
    """
    One cell's score. `check_report` is a checks.CheckReport, `judge_record`
    a judge.JudgeRecord or None.
    """
    cell = ScoredCell(
        scenario_id=scenario.id,
        model_id=model_id,
        task=scenario.task,
        status="scored",
        rubric_hash=rubric.rubric_hash,
    )

    # A gate failure is an earned zero: the model DID produce something and it
    # was unusable. This is the one case where a 0 is honest.
    if not check_report.passed:
        cell.status = "invalid"
        cell.score = 0.0
        cell.note = "failed deterministic gate(s): " + ", ".join(check_report.failed_gates)
        return cell

    judged_ok = judge_record is not None and judge_record.status == "judged"
    judge_scores = (
        {c.name: c.score for c in judge_record.criteria} if judged_ok else {}
    )

    # Any criterion the judge was supposed to score but we have no judgement
    # for makes the whole cell unjudged - a partially-filled row is worse than
    # an honest gap, because it looks complete.
    if rubric.judged_criteria and not judged_ok:
        cell.status = "unjudged"
        cell.note = (
            judge_record.error
            if judge_record is not None and judge_record.error
            else "no judge result for this cell"
        )
        return cell

    measurements = check_report.measurements
    raw: dict[str, float] = {}
    unmeasured: set[str] = set()

    for c in rubric.criteria:
        measured_value: float | None = None
        if c.measured:
            if c.measurement == "normalized_wer":
                v = measurements.get("normalized_wer")
                measured_value = apply_scale(c.scale, float(v)) if v is not None else None
            elif c.measurement == "mos":
                v = measurements.get("audio_quality_1_5")
                measured_value = apply_scale(c.scale, float(v)) if v is not None else None
            else:
                v = measurements.get(c.measurement)
                measured_value = apply_scale(c.scale, float(v)) if v is not None else None

        if c.scored_by == "measurement":
            if measured_value is None:
                unmeasured.add(c.key)
                continue
            raw[c.key] = measured_value
        elif c.scored_by == "judge":
            raw[c.key] = judge_scores[c.key]
        else:  # hybrid
            j = judge_scores.get(c.key)
            if measured_value is None and j is None:
                unmeasured.add(c.key)
                continue
            if measured_value is None:
                raw[c.key] = j
                cell.note = (cell.note + f" {c.key}: judge half only;").strip()
            elif j is None:
                raw[c.key] = measured_value
            else:
                b = c.blend or {"measurement": 0.5, "judge": 0.5}
                raw[c.key] = b["measurement"] * measured_value + b["judge"] * j

    base_weights = {c.key: c.weight for c in rubric.criteria}
    weights = _redistribute(base_weights, unmeasured) if unmeasured else base_weights
    if not weights:
        cell.status = "unjudged"
        cell.note = "no criterion could be scored"
        return cell

    cell.criterion_scores = raw
    cell.effective_weights = weights
    cell.unmeasured = sorted(unmeasured)
    cell.score = sum(raw[k] * w for k, w in weights.items() if k in raw)

    # Naturalness and prosody are the least-validated link in the chain. Until
    # two humans have agreed with the judge on five clips, a cell whose score
    # leans on those criteria is marked untrusted and the report badges it.
    gated = set(rubric.calibration_gated_keys)
    if gated and not calibration_passed and gated & set(raw):
        cell.calibration_trusted = False
        if unmeasured:
            cell.note = (cell.note + " weights redistributed;").strip()
    elif unmeasured:
        cell.note = (cell.note + f" unmeasured: {sorted(unmeasured)}, weights redistributed;").strip()
    return cell


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

@dataclass
class ModelSummary:
    model_id: str
    task: str
    mean: float | None
    worst: float | None
    below_five: int
    judged: int
    attempted: int
    invalid: int
    unjudged: int
    calibration_trusted: bool

    @property
    def coverage(self) -> float:
        return self.judged / self.attempted if self.attempted else 0.0


def summarise(cells: list[ScoredCell], model_id: str, task: str) -> ModelSummary:
    mine = [c for c in cells if c.model_id == model_id and c.task == task]
    # invalid cells are a real 0 and DO count toward the mean; unjudged cells
    # have no number and are excluded from it entirely.
    counted = [c for c in mine if c.status in ("scored", "invalid") and c.score is not None]
    scores = [c.score for c in counted]
    return ModelSummary(
        model_id=model_id,
        task=task,
        mean=(sum(scores) / len(scores)) if scores else None,
        worst=min(scores) if scores else None,
        below_five=sum(1 for s in scores if s < 5.0),
        judged=len(counted),
        attempted=len(mine),
        invalid=sum(1 for c in mine if c.status == "invalid"),
        unjudged=sum(1 for c in mine if c.status == "unjudged"),
        calibration_trusted=all(c.calibration_trusted for c in counted) if counted else True,
    )


@dataclass
class Paired:
    a: str
    b: str
    wins: int
    ties: int
    losses: int
    compared: int

    @property
    def decided(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.decided if self.decided else None


def paired_wtl(cells: list[ScoredCell], a: str, b: str, task: str) -> Paired:
    """Both models answered the identical scenario, or the pair is skipped."""
    by_model: dict[str, dict[str, ScoredCell]] = {a: {}, b: {}}
    for c in cells:
        if c.task == task and c.model_id in by_model and c.score is not None:
            by_model[c.model_id][c.scenario_id] = c
    shared = sorted(set(by_model[a]) & set(by_model[b]))
    wins = ties = losses = 0
    for sid in shared:
        delta = by_model[a][sid].score - by_model[b][sid].score
        if abs(delta) <= TIE_BAND:
            ties += 1
        elif delta > 0:
            wins += 1
        else:
            losses += 1
    return Paired(a=a, b=b, wins=wins, ties=ties, losses=losses, compared=len(shared))


def sign_test_p(wins: int, decided: int) -> float | None:
    """
    Two-sided sign test: given W wins out of D decided scenarios, how likely
    is a split at least this lopsided by coin-flip? Quoted only at D >= 10 -
    below that the report prints the raw counts and no p-value.
    """
    if decided < SIGN_TEST_MIN_N:
        return None
    k = max(wins, decided - wins)
    tail = sum(math.comb(decided, i) for i in range(k, decided + 1))
    return min(1.0, 2.0 * tail / (2**decided))


@dataclass
class Verdict:
    task: str
    winner: str | None
    loser: str | None
    reason: str
    mean_gap: float | None
    paired: Paired | None
    p_value: float | None
    quality_trusted: bool
    coverage_ok: bool


def verdict(
    summaries: list[ModelSummary], cells: list[ScoredCell], task: str, calibration_passed: bool
) -> Verdict:
    ranked = [s for s in summaries if s.task == task and s.mean is not None]
    if len(ranked) < 2:
        return Verdict(task, None, None, "fewer than two models produced a score", None, None, None, calibration_passed, False)
    ranked.sort(key=lambda s: s.mean, reverse=True)
    top, second = ranked[0], ranked[1]

    pair = paired_wtl(cells, top.model_id, second.model_id, task)
    gap = top.mean - second.mean
    p = sign_test_p(pair.wins, pair.decided)
    rate = pair.win_rate

    coverage_ok = top.coverage >= COVERAGE_DOOR
    mean_door = gap >= MEAN_GAP_DOOR
    win_door = rate is not None and rate >= WIN_RATE_DOOR

    if not coverage_ok:
        reason = (
            f"{top.model_id} leads on the mean but was scored on only "
            f"{top.judged}/{top.attempted} scenarios ({top.coverage:.0%}); a winner needs "
            f"{COVERAGE_DOOR:.0%} coverage"
        )
        return Verdict(task, None, None, reason, gap, pair, p, calibration_passed, False)

    if mean_door or win_door:
        doors = []
        if mean_door:
            doors.append(f"mean gap {gap:.3f} >= {MEAN_GAP_DOOR}")
        if win_door:
            doors.append(f"{pair.wins} wins of {pair.decided} decided ({rate:.0%} >= {WIN_RATE_DOOR:.0%})")
        reason = f"{top.model_id} beats {second.model_id}: " + " and ".join(doors)
        if p is not None:
            reason += f" (sign test p = {p:.4f})"
        if not calibration_passed:
            reason += (
                " - PROVISIONAL: the voice calibration gate has not passed, so the "
                "naturalness and prosody contributions are not yet trusted"
            )
        return Verdict(task, top.model_id, second.model_id, reason, gap, pair, p, calibration_passed, True)

    rate_txt = f"{rate:.0%}" if rate is not None else "n/a"
    reason = (
        f"tie: mean gap {gap:.3f} is inside the {MEAN_GAP_DOOR} band and "
        f"{top.model_id} won {pair.wins} of {pair.decided} decided ({rate_txt}), below the "
        f"{WIN_RATE_DOOR:.0%} door. Decide on cost, latency, reliability and worst case."
    )
    return Verdict(task, None, None, reason, gap, pair, p, calibration_passed, True)
