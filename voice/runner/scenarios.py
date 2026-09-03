"""
Scenario loading and validation (plan v1.2 section 06).

One scenario = one row in the report. A scenario says what to generate, what
a good answer looks like, and how it should be judged. NOTHING in it names a
model - that is what keeps "same input to every model" structurally true
rather than a promise.

TWO INPUT FORMATS, ONE OBJECT.
  scenarios/*.yaml  full fidelity: params, per-scenario criteria/weights, tags
  scenarios/*.csv   one row per scenario - id, task, script, style, language,
                    expected, max_wer - for the case where a non-developer is
                    writing twenty scenarios in a spreadsheet.
Both produce the same Scenario, so nothing downstream knows or cares which
was used. The CSV path fills plan defaults for everything it does not carry
and records `source_format` so the manifest says where each scenario came from.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VOICE_TASKS = {
    # Built (plan section 05).
    "text_to_speech",
    "styled_tts",
    # Reserved: legal values, no rubric override yet.
    "cloned_voice_tts",
    "multi_speaker",
    "speech_to_speech",
    "long_form",
}
IMAGE_TASKS = {
    "text_to_image",
    "image_edit",
    "inpaint_mask",
    "reference_style",
    "compose_multi",
    "upscale_restore",
}
TASKS_BY_MODALITY = {"voice": VOICE_TASKS, "image": IMAGE_TASKS}

# Defaults the CSV loader fills in. Every one of these is overridable by
# using the YAML form instead; they exist so a spreadsheet row is a complete,
# runnable scenario rather than a half-specified one.
CSV_DEFAULT_CHECKS: dict[str, Any] = {
    "duration_s": {"min": 1.0, "max": 120.0},
    "max_silence_s": 2.0,
    "no_clipping": True,
}
CSV_DEFAULT_PARAMS: dict[str, Any] = {
    "voice": "female_mid_warm",
    "format": "wav",
    "sample_rate": 24000,
    "speed": 1.0,
}


@dataclass(frozen=True)
class Scenario:
    id: str
    modality: str
    task: str
    title: str
    # The exact string sent to every model - the script for voice, the prompt
    # for image. Read once, passed by value, never edited per model.
    text: str
    expected: str
    language: str | None = None
    style: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, Any] = field(default_factory=dict)
    criteria_override: list[str] | None = None
    weights_override: dict[str, float] | None = None
    tags: tuple[str, ...] = ()
    source_path: str = ""
    source_format: str = "yaml"

    @property
    def scenario_hash(self) -> str:
        """Identity of the INPUT, independent of file formatting."""
        canon = " ".join(
            [
                self.id,
                self.modality,
                self.task,
                self.text,
                self.expected,
                self.language or "",
                self.style or "",
                repr(sorted(self.params.items())),
                repr(sorted((k, repr(v)) for k, v in self.checks.items())),
            ]
        )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    @property
    def max_wer(self) -> float | None:
        v = self.checks.get("max_wer")
        return float(v) if v is not None else None


class ScenarioError(ValueError):
    pass


def _require(doc: dict, key: str, path: Path) -> Any:
    if key not in doc or doc[key] in (None, ""):
        raise ScenarioError(f"{path}: scenario is missing required field '{key}'")
    return doc[key]


def _validate_weights(sid: str, weights: dict[str, float] | None) -> None:
    if not weights:
        return
    total = sum(float(v) for v in weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ScenarioError(
            f"scenario '{sid}': weights sum to {total:.6f}, expected 1.0. The "
            f"loader rejects rather than normalising - a normalised typo is a "
            f"scoring bug that never announces itself."
        )


def _from_yaml(path: Path) -> Scenario:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sid = str(_require(doc, "id", path))
    modality = str(_require(doc, "modality", path))
    task = str(_require(doc, "task", path))

    known = TASKS_BY_MODALITY.get(modality)
    if known is None:
        raise ScenarioError(f"{path}: unknown modality '{modality}'")
    if task not in known:
        raise ScenarioError(
            f"{path}: task '{task}' is not a legal {modality} task. Legal: {sorted(known)}"
        )

    if modality == "voice":
        inp = doc.get("input") or {}
        if not isinstance(inp, dict) or not str(inp.get("script") or "").strip():
            raise ScenarioError(
                f"{path}: a voice scenario needs `input.script` - the exact words to speak"
            )
        text = str(inp["script"]).strip()
        language = inp.get("language")
        style = inp.get("style")
    else:
        text = str(_require(doc, "prompt", path)).strip()
        language = None
        style = None

    weights = doc.get("weights")
    _validate_weights(sid, weights)

    # PARSED, VALIDATED, THEN IGNORED - until 2026-09-03, when a scenario
    # could declare `weights`, have the loader reject anything not summing to
    # 1.0, and then have them silently not applied because nothing downstream
    # reads them. Validation theatre is worse than no validation: it tells an
    # author their re-weighting is correct while scoring something else.
    #
    # Rejecting is the honest state until the scoring path reads them. The
    # plan does specify the feature (v1.2 s13, "a scenario may re-weight,
    # never invent, criteria"), so this is a NOT-YET, not a refusal - and the
    # message says so rather than pretending the field is unknown.
    if doc.get("criteria") or weights:
        raise ScenarioError(
            f"{path}: scenario '{sid}' declares per-scenario `criteria`/`weights`. "
            f"The scoring path does not read them yet, so they would be silently "
            f"ignored and the run would be scored on the task rubric instead. "
            f"Remove them, or override at the task level in "
            f"configs/rubrics/tasks/<task>.yaml, which IS applied."
        )

    return Scenario(
        id=sid,
        modality=modality,
        task=task,
        title=str(doc.get("title") or sid),
        text=text,
        expected=str(_require(doc, "expected", path)).strip(),
        language=language,
        style=style,
        params=dict(doc.get("params") or {}),
        checks=dict(doc.get("checks") or {}),
        criteria_override=list(doc["criteria"]) if doc.get("criteria") else None,
        weights_override={k: float(v) for k, v in weights.items()} if weights else None,
        tags=tuple(doc.get("tags") or ()),
        source_path=str(path),
        source_format="yaml",
    )


CSV_COLUMNS = ("id", "task", "script", "style", "language", "expected", "max_wer")


def _from_csv(path: Path) -> list[Scenario]:
    """
    One row per scenario. Columns: id, task, script, style, language,
    expected, max_wer. Extra columns are ignored so a working spreadsheet can
    carry notes; a MISSING required column is an error naming the column,
    because a silently-empty script would generate a silently-wrong clip.
    """
    out: list[Scenario] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers = {(h or "").strip() for h in (reader.fieldnames or [])}
        missing = [c for c in ("id", "task", "script", "expected") if c not in headers]
        if missing:
            raise ScenarioError(
                f"{path}: CSV is missing required column(s) {missing}. "
                f"Expected header: {','.join(CSV_COLUMNS)}"
            )
        for lineno, row in enumerate(reader, start=2):
            sid = (row.get("id") or "").strip()
            if not sid or sid.startswith("#"):
                continue  # blank spacer or commented-out row
            script = (row.get("script") or "").strip()
            if not script:
                raise ScenarioError(f"{path}:{lineno}: scenario '{sid}' has an empty script")
            task = (row.get("task") or "text_to_speech").strip()
            if task not in VOICE_TASKS:
                raise ScenarioError(
                    f"{path}:{lineno}: task '{task}' is not a legal voice task. "
                    f"Legal: {sorted(VOICE_TASKS)}"
                )
            checks = dict(CSV_DEFAULT_CHECKS)
            raw_wer = (row.get("max_wer") or "").strip()
            if raw_wer:
                try:
                    checks["max_wer"] = float(raw_wer)
                except ValueError as exc:
                    raise ScenarioError(
                        f"{path}:{lineno}: scenario '{sid}' max_wer={raw_wer!r} is not a number"
                    ) from exc
            style = (row.get("style") or "").strip() or None
            out.append(
                Scenario(
                    id=sid,
                    modality="voice",
                    task=task,
                    title=(row.get("title") or "").strip() or sid,
                    text=script,
                    expected=(row.get("expected") or "").strip(),
                    language=(row.get("language") or "").strip() or None,
                    style=style,
                    params=dict(CSV_DEFAULT_PARAMS),
                    checks=checks,
                    tags=("csv",),
                    source_path=f"{path}:{lineno}",
                    source_format="csv",
                )
            )
    return out


def load_scenarios(root: Path, modality: str | None = None) -> list[Scenario]:
    """
    Load every scenario under `root` (a directory or a single file), filtered
    to `modality` when given. Sorted by id so a run's matrix is stable.

    Duplicate ids are fatal: an id appears in every filename, telemetry row
    and report cell, and two scenarios sharing one would silently overwrite
    each other's outputs on disk.
    """
    root = Path(root)
    paths = (
        [root]
        if root.is_file()
        else sorted(
            p for p in root.rglob("*") if p.suffix.lower() in (".yaml", ".yml", ".csv")
        )
    )
    scenarios: list[Scenario] = []
    for p in paths:
        if p.suffix.lower() == ".csv":
            scenarios.extend(_from_csv(p))
        else:
            scenarios.append(_from_yaml(p))

    if modality:
        scenarios = [s for s in scenarios if s.modality == modality]

    seen: dict[str, str] = {}
    for s in scenarios:
        if s.id in seen:
            raise ScenarioError(
                f"duplicate scenario id '{s.id}' in {s.source_path} and {seen[s.id]} - "
                f"ids appear in filenames and telemetry rows and must be unique"
            )
        seen[s.id] = s.source_path
    return sorted(scenarios, key=lambda s: s.id)
