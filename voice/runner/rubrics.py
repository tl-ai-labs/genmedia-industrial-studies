"""
Rubric loading, merging, hashing and the measurement->score scales
(plan v1.2 §13, §14).

A rubric is the answer to "what are we scoring, how much does each part
count, and who decides it". It lives entirely in configs/rubrics/ so that
changing a weight is a config edit that re-scores from stored criterion
scores — no regeneration, no re-judging, no spend (§14).

THE HASH. Every score record stamps `rubric_hash`. It is taken over the
MERGED, effective rubric — base + task override — not the base file, because
the merged form is what actually produced the number. Attributes are sorted
before hashing so a reordered YAML file is the same rubric.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

WEIGHT_TOLERANCE = 1e-6
SCORED_BY = {"measurement", "judge", "hybrid"}


@dataclass(frozen=True)
class Criterion:
    key: str
    label: str
    weight: float
    scored_by: str
    description: str = ""
    measurement: str | None = None
    scale: dict[str, Any] | None = None
    blend: dict[str, float] | None = None
    calibration_gated: bool = False

    @property
    def judged(self) -> bool:
        """Does the AI judge see this criterion at all?"""
        return self.scored_by in ("judge", "hybrid")

    @property
    def measured(self) -> bool:
        return self.scored_by in ("measurement", "hybrid")


@dataclass(frozen=True)
class Rubric:
    modality: str
    task: str
    criteria: tuple[Criterion, ...]
    rubric_hash: str
    source_files: tuple[str, ...] = field(default=())

    def by_key(self, key: str) -> Criterion | None:
        return next((c for c in self.criteria if c.key == key), None)

    @property
    def judged_criteria(self) -> tuple[Criterion, ...]:
        return tuple(c for c in self.criteria if c.judged)

    @property
    def measured_criteria(self) -> tuple[Criterion, ...]:
        return tuple(c for c in self.criteria if c.measured)

    @property
    def calibration_gated_keys(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.criteria if c.calibration_gated)


def _canonical(modality: str, task: str, criteria: list[Criterion]) -> str:
    ordered = sorted(criteria, key=lambda c: c.key)
    return json.dumps(
        {
            "modality": modality,
            "task": task,
            "criteria": [
                {
                    "key": c.key,
                    "label": c.label,
                    "weight": c.weight,
                    "scored_by": c.scored_by,
                    "measurement": c.measurement,
                    "scale": c.scale,
                    "blend": c.blend,
                    "calibration_gated": c.calibration_gated,
                }
                for c in ordered
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _merge_criteria(base: list[dict], override: list[dict]) -> list[dict]:
    """
    Override entries replace base entries with the same key and may add new
    ones. An override entry that only carries `weight` keeps everything else
    from the base — that is the point of an override file.
    """
    merged: dict[str, dict] = {c["key"]: dict(c) for c in base}
    order: list[str] = [c["key"] for c in base]
    for entry in override:
        key = entry.get("key")
        if not key:
            raise ValueError("rubric override: every criterion needs a `key`")
        if key in merged:
            merged[key].update(entry)
        else:
            merged[key] = dict(entry)
            order.append(key)
    return [merged[k] for k in order]


def _build(modality: str, task: str, raw: list[dict], sources: list[str]) -> Rubric:
    criteria: list[Criterion] = []
    for entry in raw:
        key = entry.get("key")
        if not key:
            raise ValueError(f"rubric {modality}/{task}: a criterion has no `key`")
        scored_by = entry.get("scored_by")
        if scored_by not in SCORED_BY:
            raise ValueError(
                f"rubric {modality}/{task}: criterion '{key}' has scored_by="
                f"{scored_by!r}, expected one of {sorted(SCORED_BY)}"
            )
        weight = entry.get("weight")
        if not isinstance(weight, (int, float)) or not 0.0 <= float(weight) <= 1.0:
            raise ValueError(
                f"rubric {modality}/{task}: criterion '{key}' weight must be a "
                f"number in 0..1, got {weight!r}"
            )
        if scored_by in ("measurement", "hybrid") and not entry.get("measurement"):
            raise ValueError(
                f"rubric {modality}/{task}: criterion '{key}' is scored_by="
                f"'{scored_by}' but names no `measurement`. A criterion code is "
                f"meant to compute must say what it computes from."
            )
        if scored_by == "hybrid":
            blend = entry.get("blend") or {}
            total = float(blend.get("measurement", 0)) + float(blend.get("judge", 0))
            if abs(total - 1.0) > 1e-9:
                raise ValueError(
                    f"rubric {modality}/{task}: criterion '{key}' blend must sum "
                    f"to 1.0, got {total}"
                )
        criteria.append(
            Criterion(
                key=key,
                label=entry.get("label", key.replace("_", " ").capitalize()),
                weight=float(weight),
                scored_by=scored_by,
                description=(entry.get("description") or "").strip(),
                measurement=entry.get("measurement"),
                scale=entry.get("scale"),
                blend=entry.get("blend"),
                calibration_gated=bool(entry.get("calibration_gated", False)),
            )
        )

    total = sum(c.weight for c in criteria)
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        # Deliberately NOT normalised. A normalised typo is a scoring bug that
        # never announces itself (§06).
        raise ValueError(
            f"rubric {modality}/{task}: weights sum to {total:.6f}, expected "
            f"exactly 1.0. Fix the weights — the loader will not normalise them "
            f"for you, because a silently normalised typo is a scoring bug that "
            f"never announces itself."
        )
    if len({c.key for c in criteria}) != len(criteria):
        raise ValueError(f"rubric {modality}/{task}: duplicate criterion keys")

    canon = _canonical(modality, task, criteria)
    return Rubric(
        modality=modality,
        task=task,
        criteria=tuple(criteria),
        rubric_hash=hashlib.sha256(canon.encode("utf-8")).hexdigest(),
        source_files=tuple(sources),
    )


def load_rubric(configs_dir: Path, modality: str, task: str) -> Rubric:
    """
    Load the effective rubric for (modality, task). Base file is required;
    the per-task override is optional — a task with no override file simply
    uses the base five criteria.
    """
    base_path = Path(configs_dir) / "rubrics" / f"{modality}.yaml"
    if not base_path.exists():
        raise FileNotFoundError(f"no base rubric for modality '{modality}' at {base_path}")
    base_doc = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}
    raw = list(base_doc.get("criteria") or [])
    sources = [str(base_path)]

    task_path = Path(configs_dir) / "rubrics" / "tasks" / f"{task}.yaml"
    if task_path.exists():
        task_doc = yaml.safe_load(task_path.read_text(encoding="utf-8")) or {}
        raw = _merge_criteria(raw, list(task_doc.get("criteria") or []))
        sources.append(str(task_path))

    return _build(modality, task, raw, sources)


# --------------------------------------------------------------------------
# Measurement -> 0..10 scales. Declared in the rubric YAML, applied here.
# --------------------------------------------------------------------------

def apply_scale(scale: dict[str, Any] | None, value: float) -> float:
    """Map one raw measurement onto the 0..10 criterion scale."""
    if scale is None:
        raise ValueError("apply_scale: criterion has no `scale` block")
    kind = scale.get("kind")
    if kind == "piecewise_linear":
        pts = [(float(x), float(y)) for x, y in scale["points"]]
        pts.sort(key=lambda p: p[0])
        if value <= pts[0][0]:
            return pts[0][1]
        if value >= pts[-1][0]:
            return pts[-1][1]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= value <= x1:
                if x1 == x0:
                    return y1
                t = (value - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return pts[-1][1]
    if kind == "linear_map":
        lo, hi = (float(v) for v in scale["from"])
        out_lo, out_hi = (float(v) for v in scale["to"])
        if hi == lo:
            return out_lo
        t = (value - lo) / (hi - lo)
        return max(out_lo, min(out_hi, out_lo + t * (out_hi - out_lo)))
    raise ValueError(f"apply_scale: unknown scale kind {kind!r}")
