"""
Read a lane's run folder into the pairs the panel shows.

THE CONTRACT THIS RELIES ON. Every lane writes the same run folder
(`<module>/runs/<run-id>/`): `manifest.json` names the modality,
`scenarios/*.yaml` is the frozen copy of what was actually sent,
`scores.jsonl` carries one scored row per scenario x model, and the media
sits at `outputs/<modality>/<scenario-id>/<model-id>.<ext>`. Nothing here
imports a lane's own code - modules do not import from each other - so the
panel reads the folder the way a stranger would, and a lane that drifts
from the contract shows up as a skipped scenario with a reason, never as a
wrong pair.

WHY A PAIR, NOT A LIST. The study is pairwise - two arms per run - and a
thumbs-up is a pairwise instrument. A scenario with one arm missing its media
(a failed generation is a recorded failure, not a silent gap) cannot be shown
to a reviewer without inventing a comparison, so it is skipped and counted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Which files the panel can show, by kind. Anything else under `inputs:`
# (a bbox, a wer_reference string) is not media and is not shown.
MEDIA_KINDS: dict[str, str] = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".mp4": "video", ".webm": "video", ".mov": "video",
    ".wav": "audio", ".mp3": "audio", ".m4a": "audio", ".ogg": "audio",
}

# Video reports write compact previews beside the run (`--self-contained`).
# They are the right size for a web panel; the originals are not.
PREVIEW_DIRNAME = "previews"

INPUT_ORDER = {"source": 0, "reference": 1, "mask": 2}


@dataclass
class Arm:
    model_id: str
    media: Path | None
    score: float | None
    status: str | None


@dataclass
class Pair:
    lane: str
    run_id: str
    run_dir: Path
    scenario_id: str
    task: str
    title: str
    brief: str
    inputs: list[tuple[str, Path]]   # (label, path) - source, reference, mask
    arms: list[Arm]                  # exactly two, both with media
    industry: str = ""               # primary industry from the lane's industry_map.yaml

    @property
    def kind(self) -> str:
        assert self.arms[0].media is not None
        return MEDIA_KINDS[self.arms[0].media.suffix.lower()]


@dataclass
class RunData:
    lane: str
    run_id: str
    run_dir: Path
    pairs: list[Pair] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (scenario, why)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # A half-written trailing line after a crash; every lane's own
            # reader tolerates it the same way.
            continue
    return rows


def _brief(doc: dict[str, Any]) -> str:
    """The text the reviewer must see: image/video keep it in `prompt`,
    voice keeps the script under `input.script`."""
    if doc.get("prompt"):
        return str(doc["prompt"]).strip()
    inp = doc.get("input") or {}
    if isinstance(inp, dict) and inp.get("script"):
        return str(inp["script"]).strip()
    if doc.get("text"):
        return str(doc["text"]).strip()
    return ""


def _frozen_scenarios(run_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    d = run_dir / "scenarios"
    for f in sorted(d.glob("*.yaml")) if d.exists() else []:
        try:
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if isinstance(doc, dict) and doc.get("id"):
            out[str(doc["id"])] = doc
    return out


def _find_media(run_dir: Path, modality: str, scenario_id: str, model_id: str) -> Path | None:
    """Prefer the compact preview when the lane wrote one; otherwise the
    output itself. A variant scenario's directory is literally named with
    the `#` (`vr-ecom-06#bare`), so the path is built, not URL-decoded."""
    preview = run_dir / PREVIEW_DIRNAME / f"{scenario_id}--{model_id}.mp4"
    if preview.exists():
        return preview
    out_dir = run_dir / "outputs" / modality / scenario_id
    if not out_dir.is_dir():
        return None
    for p in sorted(out_dir.iterdir()):
        if p.stem == model_id and p.suffix.lower() in MEDIA_KINDS:
            return p
    return None


def _resolve_input(run_dir: Path, scenario_id: str, rel: str) -> Path | None:
    """The run may have frozen the input under `inputs/<scenario>/`; if not,
    the path is relative to the module root (`<module>/assets/bank/...`)."""
    name = Path(rel).name
    frozen = run_dir / "inputs" / scenario_id / name
    if frozen.exists():
        return frozen
    module_root = run_dir.parent.parent
    cand = module_root / rel
    if cand.exists():
        return cand
    return None


def _industry_map(module_root: Path) -> dict[str, str]:
    """scenario id -> primary industry, from `<module>/configs/industry_map.yaml`.
    Missing file = no industries, never an error: the panel works without it."""
    f = module_root / "configs" / "industry_map.yaml"
    if not f.exists():
        return {}
    try:
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    out: dict[str, str] = {}
    for sid, entry in (doc.get("scenarios") or {}).items():
        if isinstance(entry, dict) and entry.get("primary"):
            out[str(sid)] = str(entry["primary"])
        elif isinstance(entry, str):
            out[str(sid)] = entry
    return out


def load_run(run_dir: Path, lane: str | None = None) -> RunData:
    run_dir = Path(run_dir).resolve()
    manifest = {}
    if (run_dir / "manifest.json").exists():
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    modality = str(manifest.get("modality") or lane or "")
    if not modality:
        raise ValueError(f"{run_dir}: no modality in manifest.json and no lane given")
    lane = lane or modality
    run_id = str(manifest.get("run_id") or run_dir.name)
    data = RunData(lane=lane, run_id=run_id, run_dir=run_dir)

    # Last row wins per (scenario, model): a re-scored run appends, it does
    # not rewrite, and the latest row is the one the lane's report used.
    scores: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _read_jsonl(run_dir / "scores.jsonl"):
        scores[(str(row.get("scenario_id")), str(row.get("model_id")))] = row

    scenarios = _frozen_scenarios(run_dir)
    if not scenarios:
        raise ValueError(f"{run_dir}: no frozen scenarios under scenarios/")
    industries = _industry_map(run_dir.parent.parent)

    # Which models took part: every model id that appears in scores or that
    # left a file in outputs/. Score-less arms still get shown if their
    # media exists - a reviewer can prefer a clip the judge refused to score.
    models_by_scenario: dict[str, set[str]] = {}
    for (sid, mid) in scores:
        models_by_scenario.setdefault(sid, set()).add(mid)
    out_root = run_dir / "outputs" / modality
    if out_root.is_dir():
        for sdir in out_root.iterdir():
            if not sdir.is_dir():
                continue
            for p in sdir.iterdir():
                if p.suffix.lower() in MEDIA_KINDS:
                    models_by_scenario.setdefault(sdir.name, set()).add(p.stem)

    for sid in sorted(models_by_scenario):
        # A variant scenario (`vr-ecom-06#bare`) is frozen under its parent id.
        parent = sid.split("#", 1)[0]
        doc = scenarios.get(sid) or scenarios.get(parent)
        if doc is None:
            data.skipped.append((sid, "no frozen scenario"))
            continue
        models = sorted(models_by_scenario[sid])
        if len(models) != 2:
            data.skipped.append((sid, f"{len(models)} arms, need exactly 2"))
            continue
        arms = []
        for mid in models:
            row = scores.get((sid, mid), {})
            arms.append(Arm(model_id=mid,
                            media=_find_media(run_dir, modality, sid, mid),
                            score=(float(row["score"]) if row.get("score") is not None else None),
                            status=row.get("status")))
        missing = [a.model_id for a in arms if a.media is None]
        if missing:
            data.skipped.append((sid, "no media for " + ", ".join(missing)))
            continue
        if MEDIA_KINDS[arms[0].media.suffix.lower()] != MEDIA_KINDS[arms[1].media.suffix.lower()]:  # type: ignore[union-attr]
            data.skipped.append((sid, "arms are different media kinds"))
            continue
        # Shown source first, then reference, then anything else - the
        # reviewer reads "what was edited" before "what to apply".
        inputs: list[tuple[str, Path]] = []
        raw_inputs = doc.get("inputs") or {}
        for label, rel in sorted(raw_inputs.items(), key=lambda kv: INPUT_ORDER.get(kv[0], 9)):
            if not isinstance(rel, str) or Path(rel).suffix.lower() not in MEDIA_KINDS:
                continue
            p = _resolve_input(run_dir, sid, rel)
            if p is None:
                p = _resolve_input(run_dir, parent, rel)
            if p is not None:
                inputs.append((str(label), p))
        data.pairs.append(Pair(
            lane=lane, run_id=run_id, run_dir=run_dir, scenario_id=sid,
            task=str(doc.get("task") or ""), title=str(doc.get("title") or ""),
            brief=_brief(doc), inputs=inputs, arms=arms,
            industry=industries.get(sid) or industries.get(parent) or "",
        ))
    return data


def read_scores(run_dir: Path) -> dict[tuple[str, str], float | None]:
    """(scenario_id, model_id) -> judge score, last row wins. Used by the
    correlation, which reads scores.jsonl directly as the plan asks."""
    out: dict[tuple[str, str], float | None] = {}
    for row in _read_jsonl(Path(run_dir) / "scores.jsonl"):
        s = row.get("score")
        out[(str(row.get("scenario_id")), str(row.get("model_id")))] = (
            float(s) if s is not None else None)
    return out
