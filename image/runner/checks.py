"""Deterministic-check dispatch: gates (valid at all?) and measures (facts
with a right answer). The implementations live in the per-modality packages
(runner/image/checks.py, runner/video/checks.py); this module owns the
contract (CheckOutcome) and the suite registry.

Suites are selected by (modality, task) — adding a task means adding a suite
entry, not touching the runner.

Gate failure  -> cell state `invalid`, score 0, the judge is never called.
Measures      -> recorded facts, injected into the judge prompt, and scored
                 by scoring.py for `measured` criteria.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CheckOutcome:
    gates: list = field(default_factory=list)      # [{"gate", "passed", "detail"}]
    measures: dict = field(default_factory=dict)   # facts for judge + scoring

    @property
    def passed(self) -> bool:
        return all(g["passed"] for g in self.gates)


def _gate(name: str, passed: bool, detail: str = "") -> dict:
    return {"gate": name, "passed": bool(passed), "detail": detail}


# --------------------------------------------------------------------------
# Suite selection by (modality, task) — dotted "module:function" strings so
# a modality package is only imported when its lane actually runs
# --------------------------------------------------------------------------

CHECK_SUITES = {
    ("image", "text_to_image"): "runner.image.checks:check_image",
    ("image", "image_edit"): "runner.image.checks:check_image",
    # video is a sibling project (../video/), not a package here
    # ("voice", "text_to_speech"): "runner.voice.checks:check_voice", Phase 2
}


def run_checks(scenario, output_path: Path, assets: dict | None = None) -> CheckOutcome:
    ref = CHECK_SUITES.get((scenario.modality, scenario.task))
    if ref is None:
        raise ValueError(f"no check suite for ({scenario.modality}, {scenario.task}) "
                         f"— reserved tasks gain one when a real use case arrives")
    module_name, func_name = ref.split(":")
    suite = getattr(importlib.import_module(module_name), func_name)
    return suite(scenario, output_path, assets)
