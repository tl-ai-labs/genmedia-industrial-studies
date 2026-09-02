"""Shared definitions: cell lifecycle states, run states, the task registry.

Plan §4 (lifecycle) and §5 (task types). The unit that has a lifecycle is the
cell: one scenario x one model x one task. Four terminal states, each reported
as itself — only `invalid` ever becomes a score of 0.
"""
from __future__ import annotations

# ---- cell lifecycle -------------------------------------------------------

CELL_SPINE = ["planned", "prepared", "generated", "checked", "measured", "judged", "scored"]

# terminal states and where they branch from
CELL_TERMINAL = {
    "skipped":  "planned",    # task unsupported — never attempted, n/a in the report
    "failed":   "generated",  # error / refused after retries — reliability, not quality
    "invalid":  "checked",    # a deterministic gate failed — the one earned 0
    "unjudged": "judged",     # judge unavailable — excluded from the mean, never 0
}

CELL_STATES = set(CELL_SPINE) | set(CELL_TERMINAL)

# attempt statuses recorded in telemetry (plan §10)
ATTEMPT_STATUSES = {"ok", "invalid_output", "timeout", "rate_limited", "refused", "provider_error"}

# ---- run lifecycle --------------------------------------------------------

RUN_STATES = ["draft", "planned", "running", "generated", "judged", "scored", "reported",
              "aborted",    # budget cap hit — partial run, clearly labelled
              "rejected"]   # validation / missing key — before any spend

# ---- task registry (plan §5) ---------------------------------------------
# A modality is not a use case. `task` selects required inputs, checks, the
# rubric override and the judge prompt. Reserved names are legal values of
# `task` and `supports:` and nothing more.

BUILD_TASKS = {
    "text_to_image":  {"modality": "image", "inputs": [],          "phase": "1"},
    "text_to_speech": {"modality": "voice", "inputs": [],          "phase": "1"},
    "image_edit":     {"modality": "image", "inputs": ["source"],  "phase": "2b",
                       "optional_inputs": ["mask", "bbox"]},
    "styled_tts":     {"modality": "voice", "inputs": [],          "phase": "2b"},
}

RESERVED_TASKS = {
    # image
    "inpaint_mask": "image", "reference_style": "image",
    "compose_multi": "image", "upscale_restore": "image",
    # voice
    "cloned_voice_tts": "voice", "multi_speaker": "voice",
    "speech_to_speech": "voice", "long_form": "voice",
}

ALL_TASKS = dict({t: cfg["modality"] for t, cfg in BUILD_TASKS.items()}, **RESERVED_TASKS)


def task_modality(task: str) -> str:
    if task not in ALL_TASKS:
        raise ValueError(f"unknown task {task!r}; legal tasks: {sorted(ALL_TASKS)}")
    return ALL_TASKS[task]


def task_is_buildable(task: str) -> bool:
    """Reserved tasks exist in the schema only — no checks or rubric yet."""
    return task in BUILD_TASKS
