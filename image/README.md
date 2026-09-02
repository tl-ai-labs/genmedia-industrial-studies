# genmedia-eval

Compare N image models (and, from Phase 2, N voice models) on the same
scenarios: same input to every model, outputs and telemetry on disk, a blind
hybrid judge, and **quality, cost, latency and reliability reported as four
separate columns — never blended into one**.

Implements the *GenMedia Model Comparison Plan v1.2* (31 Aug 2026).
Current state: **Phase 0 (walking skeleton) + Phase 1 (image judging)** built.
Voice is Phase 2.

## Quick start

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"

cp .env.example .env       # fill in GEMINI_API_KEY, OPENAI_API_KEY
.venv/bin/python -m pytest # whole suite runs offline, no keys, no spend

python -m runner.cli run    --modality image --scenarios scenarios/ --budget 5.00
python -m runner.cli judge  --run <run-id>
python -m runner.cli report --run <run-id> --open
python -m runner.cli cost   --run <run-id>
```

`run`, `judge` and `report` are separate commands **on purpose**: re-judging or
re-reporting never re-generates media, and a report tweak costs nothing.
`run --run <existing-id>` resumes a run — existing outputs are never paid for
twice. A missing API key or an over-budget pre-flight estimate rejects the run
*before* any call is made.

## How a score is produced (the rubric flow, in plain language)

1. **Criteria and weights live in config, not code.** The base rubric for each
   modality is `configs/rubrics/image.yaml` / `voice.yaml`; a task can override
   it with a small file in `configs/rubrics/tasks/<task>.yaml`, and a scenario
   may re-weight (never invent) criteria. Weights must sum to 1.0 — the loader
   **rejects** anything else rather than silently normalising, because a
   normalised typo is a scoring bug that never announces itself.
2. **Code measures everything that has a right answer, first and for free.**
   Does the file decode? Right size? Not blank? Does OCR read the required
   text? (Later: normalized WER, MOS, edit preservation via pHash/SSIM.) A
   failed *gate* means the cell is `invalid` — the one earned 0 — and the
   judge is never called for it. A *measurement* (like the OCR match) is a
   recorded fact.
3. **The blind AI judge scores only what code cannot measure.** Each output is
   re-labelled A/B/C in a per-scenario shuffled order, metadata stripped, no
   model or provider name anywhere in the prompt (checked mechanically). The
   judge sees the brief, the expected result, and the *measured facts injected
   as established truth*, and must return JSON with **reasoning before each
   0–10 score** so the argument comes before the number. Temperature 0, same
   judge for every output. A judge failure is `unjudged` — shown as “—”,
   excluded from the mean, **never a 0**.
4. **`scoring.py` computes the weighted total.** Measured criteria (e.g.
   `text_accuracy`) are converted from the measurement by a fixed mapping
   (OCR match 1.0 → 10, below 0.6 → 0, linear between); judge criteria come
   from the judge JSON. The scenario score is the weighted sum; the model
   score is the mean over judged scenarios only.
5. **Everything is traceable.** The sha256 of the rubric file(s) is stamped
   into every judge record and every score record, the judge prompt is hashed,
   and every cost row says which usage number and which price produced it and
   whether the usage was `api_reported` or `estimated`. Changing rubric
   *weights* re-scores stored criterion scores for free (`cli score`); changing
   rubric *text* means a new run — the judge command refuses a hash mismatch.
6. **The verdict reads two lenses.** Weighted means compress (LLM judges score
   almost everything 6.5–8.5), so the report also computes paired
   win/tie/loss on the same scenarios (tie = |Δ| ≤ 0.5). A winner needs a mean
   gap ≥ 0.5 **or** ≥ 70 % of decided scenarios (sign test quoted from n ≥ 10),
   plus ≥ 80 % coverage; otherwise it is a declared tie, broken only by facts:
   check failures → reliability → cost → latency.

## The cell lifecycle

The unit of work is the **cell**: one scenario × one model × one task.

```
planned → prepared → generated → checked → measured → judged → scored
   │                     │           │                   │
   └→ skipped            └→ failed   └→ invalid          └→ unjudged
      (task unsupported)    (error/     (gate failed:       (judge failed:
       = n/a, excluded       refused)    the one earned 0)   excluded, never 0)
```

Four terminal states, each reported as itself. Only `invalid` becomes a 0.

## Run folder — self-contained, zippable

```
runs/<run-id>/
├── manifest.json      run id, git sha, models, rubric hashes, cell states,
│                      effective per-scenario weights, budget, event log
├── scenarios/         frozen copies of every scenario THIS run used
├── inputs/            frozen + hashed source assets (edit tasks)
├── outputs/<modality>/<scenario-id>/<model-id>.png   one file per model
├── telemetry.jsonl    one row per generation attempt (retries visible)
├── checks.jsonl       one row per deterministic check result
├── judge.jsonl        one row per judge call: blind map, prompt hash, raw response
├── scores.jsonl       one row per scenario × model: criteria, weights, total
└── report.html        the deliverable — opens anywhere, no server
```

JSONL rows are append-only and outputs are immutable: a correction is a new
run. “The report says 8.4 — prove it” is a five-second answer.

## Adding a model / provider / scenario

* **Model on an existing provider**: one block in `configs/models.yaml` with
  `enabled: true`, `supports:`, `limits:` and a sourced+dated `price:`. Done.
* **New provider**: the block above **plus one adapter file** in
  `runner/adapters/` exposing `build(model_cfg, timeout_s)`. Adapters do three
  things only — translate the request, call the API once, return bytes + raw
  usage. No retries, no cost maths, no file writing. Provider SDKs appear
  nowhere else.
* **Scenarios are data, never hardcoded.** Full YAML (`scenarios/*.yaml`, plan
  §6) or a spreadsheet: a CSV with columns
  `id,task,prompt,expected,required_text` loads as a whole batch
  (`--scenarios batch.csv`).
* **New task**: add a check-suite entry keyed `(modality, task)` in
  `runner/checks.py` and (optionally) a rubric override file. The runner,
  judge, scorer and report don't change — they key off config.

## What is deliberately not here

No server, no queue, no database, no Docker. Files + JSONL + jinja2 +
`ThreadPoolExecutor` with per-provider semaphores. Reserved task names
(`inpaint_mask`, `cloned_voice_tts`, …) exist as legal schema values only.
Not built yet, by plan: voice lane (Phase 2), `image_edit`/`styled_tts`
scenarios (2b), median-of-3 re-judging on close calls (Phase 3), video.

## Honesty rules the code enforces

* Missing key → run `rejected` before any spend.
* Pre-flight cost estimate printed; `--budget` refuses up front and aborts
  mid-run (partial run clearly labelled).
* One attempt = one telemetry row; a refusal is never retried.
* `unjudged` ≠ 0; `skipped` (unsupported task) = n/a, excluded from every
  denominator; `invalid` = the one earned 0.
* Costs in integer micro-USD, derived from returned usage; anything estimated
  is labelled `estimated` in storage *and* in the report.
* Human gates before numbers are quoted (plan §11): the Phase 1 eyeball of the
  5 best + 5 worst judged outputs, and the 5-output calibration. These are
  process steps for the run owner — the tooling shows the data; a human signs
  off.
