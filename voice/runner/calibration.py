"""
The voice calibration gate (plan v1.2 section 13).

An LLM judge on image criteria is a proven pattern. An LLM judge on prosody
and naturalness is much less validated - so in the voice lane calibration is
a GATE, not polish: two people score five clips with the same rubric, and if
the judge ranks them the same way and lands within about a point, the
naturalness and prosody scores are trusted. Until then the report prints the
voice quality column with a "judge uncalibrated" badge and the decision leans
on what is already objective - normalized WER, the audio-quality measurement,
latency, cost and reliability.

TIED TO THE RUBRIC AND THE JUDGE. A calibration result is only valid for the
rubric_hash and judge model it was measured against. Edit a weight or swap
the judge and the gate reverts to uncalibrated, because the thing that was
validated no longer exists. That is the whole reason the hash is stamped on
every score record.

THE FILE IS FILLED IN BY HUMANS. `genmedia calibrate --init` writes a
template naming five clips; two reviewers listen, score, and commit it. There
is no UI, and one is not needed for five clips.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# The operational reading of "same ranking, within ~1 point". Both live here
# and are echoed into the report so a reader can see what was required.
MAX_MEAN_ABS_DIFF = 1.0
MIN_RANK_CORRELATION = 0.9
REQUIRED_REVIEWERS = 2
REQUIRED_CLIPS = 5


@dataclass
class ReviewerAgreement:
    reviewer: str
    n: int
    mean_abs_diff: float
    rank_correlation: float
    passed: bool
    reason: str


@dataclass
class CalibrationState:
    passed: bool
    reason: str
    rubric_hash: str | None = None
    judge_model: str | None = None
    reviewers: list[ReviewerAgreement] = field(default_factory=list)
    source: str | None = None

    @property
    def badge(self) -> str:
        return "calibrated" if self.passed else "judge uncalibrated"

    @property
    def as_record(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "rubric_hash": self.rubric_hash,
            "judge_model": self.judge_model,
            "source": self.source,
            "thresholds": {
                "max_mean_abs_diff": MAX_MEAN_ABS_DIFF,
                "min_rank_correlation": MIN_RANK_CORRELATION,
                "required_reviewers": REQUIRED_REVIEWERS,
                "required_clips": REQUIRED_CLIPS,
            },
            "reviewers": [
                {
                    "reviewer": r.reviewer,
                    "n": r.n,
                    "mean_abs_diff": round(r.mean_abs_diff, 3),
                    "rank_correlation": round(r.rank_correlation, 3),
                    "passed": r.passed,
                    "reason": r.reason,
                }
                for r in self.reviewers
            ],
        }


def _spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation, average ranks for ties. No scipy dependency."""

    def ranks(vals: list[float]) -> list[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        out = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        # No spread on one side - a flat set of human scores tells us nothing
        # about ranking agreement, so treat it as no evidence rather than
        # perfect evidence.
        return 0.0
    return num / (dx * dy)


def calibration_path(project_root: Path) -> Path:
    return Path(project_root) / "calibration" / "voice.yaml"


def evaluate(project_root: Path, rubric_hash: str, judge_model: str, judge_scores: dict[str, float]) -> CalibrationState:
    """
    `judge_scores` maps "<scenario_id>|<model_id>" to the judge's composite
    for that clip, taken from the run being reported.
    """
    path = calibration_path(project_root)
    if not path.exists():
        return CalibrationState(
            passed=False,
            reason=(
                f"no calibration file at {path} - run `genmedia calibrate --init --run <id>` "
                f"to generate one, have two people score the five clips, and re-run the report"
            ),
            rubric_hash=rubric_hash,
            judge_model=judge_model,
        )

    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    source = str(path)

    file_hash = str(doc.get("rubric_hash") or "")
    if file_hash and file_hash != rubric_hash:
        return CalibrationState(
            False,
            (
                "calibration was measured against a different rubric "
                f"({file_hash[:12]}...) than this run used ({rubric_hash[:12]}...). "
                "Editing a weight invalidates calibration - re-run the gate."
            ),
            rubric_hash,
            judge_model,
            source=source,
        )
    file_judge = str(doc.get("judge_model") or "")
    if file_judge and file_judge != judge_model:
        return CalibrationState(
            False,
            f"calibration was measured against judge '{file_judge}', this run used '{judge_model}'",
            rubric_hash,
            judge_model,
            source=source,
        )

    reviewers_raw = doc.get("reviewers") or []
    if len(reviewers_raw) < REQUIRED_REVIEWERS:
        return CalibrationState(
            False,
            f"{len(reviewers_raw)} reviewer(s) in {path}; the gate needs {REQUIRED_REVIEWERS}",
            rubric_hash,
            judge_model,
            source=source,
        )

    agreements: list[ReviewerAgreement] = []
    for entry in reviewers_raw:
        name = str(entry.get("name") or "unnamed")
        scores = entry.get("scores") or {}
        pairs = [
            (float(v), float(judge_scores[k]))
            for k, v in scores.items()
            if v is not None and k in judge_scores and judge_scores[k] is not None
        ]
        if len(pairs) < REQUIRED_CLIPS:
            agreements.append(
                ReviewerAgreement(
                    name,
                    len(pairs),
                    float("nan"),
                    float("nan"),
                    False,
                    f"scored {len(pairs)} clips that this run also judged; the gate needs "
                    f"{REQUIRED_CLIPS}",
                )
            )
            continue
        human = [p[0] for p in pairs]
        judge = [p[1] for p in pairs]
        mad = sum(abs(a - b) for a, b in pairs) / len(pairs)
        rho = _spearman(human, judge)
        ok = mad <= MAX_MEAN_ABS_DIFF and rho >= MIN_RANK_CORRELATION
        agreements.append(
            ReviewerAgreement(
                name,
                len(pairs),
                mad,
                rho,
                ok,
                (
                    f"mean |human - judge| = {mad:.2f} (need <= {MAX_MEAN_ABS_DIFF}), "
                    f"rank correlation = {rho:.2f} (need >= {MIN_RANK_CORRELATION})"
                ),
            )
        )

    passed = len(agreements) >= REQUIRED_REVIEWERS and all(a.passed for a in agreements)
    if passed:
        reason = (
            f"{len(agreements)} reviewers agreed with the judge on {agreements[0].n} clips "
            f"- naturalness and prosody scores are trusted"
        )
    else:
        failing = [a for a in agreements if not a.passed]
        reason = "calibration gate NOT passed: " + "; ".join(f"{a.reviewer}: {a.reason}" for a in failing)

    return CalibrationState(passed, reason, rubric_hash, judge_model, agreements, source)


def write_template(
    project_root: Path, run_id: str, rubric_hash: str, judge_model: str, clips: list[str]
) -> Path:
    """
    Write a calibration file for two reviewers to fill in. Never overwrites -
    an existing calibration is evidence, and replacing it silently would
    destroy the record of what was validated.
    """
    path = calibration_path(project_root)
    if path.exists():
        raise FileExistsError(
            f"{path} already exists. Calibration is evidence - move or delete it "
            f"deliberately if you mean to redo the gate."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Voice calibration gate (plan v1.2 section 13).",
        "#",
        "# TWO people listen to the five clips below and score each one 0-10 on overall",
        "# voice quality, using the same rubric the judge used. Do not look at the",
        "# judge's scores first. Fill in every clip for both reviewers, then re-run",
        "# `genmedia report` - the gate is evaluated automatically and the",
        '# "judge uncalibrated" badge disappears if it passes.',
        "#",
        f"# Clips are from run {run_id}. Play them from:",
        f"#   runs/{run_id}/outputs/voice/<scenario>/<model>.wav",
        "#",
        "# This file is only valid for the rubric hash and judge below. Edit a weight",
        "# in configs/rubrics/voice.yaml and the gate reverts to uncalibrated.",
        f"rubric_hash: {rubric_hash}",
        f"judge_model: {judge_model}",
        f"run_id: {run_id}",
        "",
        "reviewers:",
    ]
    for who in ("reviewer-1", "reviewer-2"):
        lines.append(f"  - name: {who}")
        lines.append("    scores:")
        for clip in clips:
            lines.append(f'      "{clip}": null')
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def pick_calibration_clips(cells: list, limit: int = REQUIRED_CLIPS) -> list[str]:
    """
    Five clips spread across the score range - the best, the worst, and three
    evenly spaced between - so reviewers see the spread rather than five
    near-identical clips that agree by accident.
    """
    scored = sorted(
        [c for c in cells if c.status == "scored" and c.score is not None], key=lambda c: c.score
    )
    if not scored:
        return []
    if len(scored) <= limit:
        picks = scored
    else:
        step = (len(scored) - 1) / (limit - 1)
        picks = [scored[round(i * step)] for i in range(limit)]
    return [f"{c.scenario_id}|{c.model_id}" for c in picks]
