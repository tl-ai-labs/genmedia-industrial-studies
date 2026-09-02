# genmedia-industrial-studies

Industrial GenMedia model comparisons — the same discipline applied to every
modality: identical inputs to every model, deterministic checks before any AI
judging, blind hybrid judging, and **quality, cost, latency and reliability
reported as four separate columns, never blended into one number**.

Implements the *GenMedia Model Comparison Plan v1.2* (31 Aug 2026). Scenarios,
rubrics and industry mappings come from the shared scenario-bank workbook in
`assets/`.

## Modules

Each modality is an independent, self-contained module — its own adapters,
checks, rubrics, scenario bank, tests and run folders. Modules do not import
from each other.

| Module   | Status        | Scope |
|----------|---------------|-------|
| `image/` | **complete**  | 60 scenarios (text-to-image + image editing), Gemini 3 Pro vs GPT Image 2, blind-judged |
| `video/` | in progress   | text-to-video first (cinematic + physics families from the bank) |
| `voice/` | planned       | TTS / styled speech (Phase 2 of the plan) |

Shared across modules:

- `assets/` — the scenario-bank workbook (scenarios, rubrics, industry
  mappings) and synthetic source assets
- evaluation standards: gates → measures → blind judge → weighted score;
  rubric weights must sum to 1.0; a failed call is a recorded failure, never a
  silent gap; runs are immutable — a correction is a new run
- storage conventions: every run is a self-contained folder
  (`<module>/runs/<run-id>/`) holding frozen scenarios, outputs, JSONL
  telemetry/checks/judge/scores, and the HTML report

## Quick start

Each module documents itself — start with [image/README.md](image/README.md).

```bash
cd image
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env        # fill in keys; .env is never committed
.venv/bin/python -m pytest  # entire suite runs offline: no keys, no spend
```

Run folders (`**/runs/`) are deliberately not committed — they are dated,
immutable artefacts. Results circulate as the generated HTML reports.
