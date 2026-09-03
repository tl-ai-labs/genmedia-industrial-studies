# Handoff — running the voice comparison without me

Everything you need to run this, add a model, or add a scenario. If something
here is wrong, the code is right and this file is stale — say so.

---

## 1 · First five minutes

```bash
cd voice
uv venv --python 3.12 .venv
VIRTUAL_ENV=$PWD/.venv uv pip install -e '.[google,dev]'
.venv/bin/python -m pytest tests/ -q          # 258 tests, offline, no key needed
```

If the tests pass, the machine works. **The whole suite runs with no API key
and no network** — so a broken checkout is always distinguishable from a
broken credential.

### Credentials

Put them in `voice/.env` — copy `voice/.env.example`, which lists every one
of them. `.env` is gitignored; never commit it:

```
ELEVENLABS_API_KEY=sk_...
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
GCP_PROJECT_ID=ai-studies-console
```

`run` checks every enabled model's credential **before spending anything** and
stops with the variable name if one is missing. That is deliberate: finding out
on call 14 of 40 means paying for 13 calls of a comparison that cannot finish.

---

## 2 · Running a comparison

Four commands, in order. They are separate on purpose: **re-judging must never
regenerate audio, and re-reporting must cost nothing.**

```bash
.venv/bin/python -m runner.cli --modality voice run       --budget 1.00
.venv/bin/python -m runner.cli --modality voice judge     --budget 0.50
.venv/bin/python -m runner.cli --modality voice report    --open
.venv/bin/python -m runner.cli --modality voice dashboard --open
```

| Command | Does | Costs |
|---|---|---|
| `run` | generates audio, runs deterministic checks, ASR, WER, audio quality. **One run per scenario** (`--bundle` for one folder covering all) | TTS + ASR |
| `judge` | blind judging of clips that PASSED their gates | judge tokens |
| `report` | scores + `report.html` for one run, and writes `summary.json` | nothing |
| `dashboard` | `runs/index.html` across every run | nothing |
| `summarise` | just `summary.json` | nothing |
| `cost` | cost breakdown for a run | nothing |
| `calibrate --init` | writes the human calibration template | nothing |

Useful flags: `--yes` skips the confirm prompt · `--run <id>` targets an older
run · `--models a,b` limits the arms · `--scenarios <dir>` uses a different
scenario set · `--budget <usd>` is a hard cap, checked before every call ·
`--bundle` puts every scenario in ONE run folder.

### One scenario, one run

**A run is the evidence for one question**: how do these models handle THIS
script. Five scenarios produce five runs, five manifests, five reports. `all`
judges and reports every one it minted.

This makes the filing agree with a rule the runner already had — the scenario
was already the unit of *completion*, so it is now the unit of *storage* too.
Re-running one scenario cannot touch another's evidence.

The fan-out confirms **once** with the total estimate, then runs unattended;
each pass is still bound by its own `--budget`.

**Aggregation moved, it did not disappear.** A single-scenario run's report has
one row and its paired win/tie/loss is n=1, which is thin by construction. The
cross-run **dashboard** is where "mean across scenarios" and the real paired
comparison live — so the dashboard is the surface you show, and a run report is
drill-in evidence.

Note this is a deliberate departure from plan v1.2 §09/§16, which shows one run
holding many scenarios and aggregating them in its report. Changed on request
2026-09-02; the plan and the code disagree here on purpose.

**Read the Repeats tab before quoting any gap.** Running the SAME scenario twice
is the only way to learn how much these scores move on their own, and until you
have done it you cannot say whether a gap between two models is a finding or the
machine breathing. Measured on voi-ret-01, 2026-09-02: ElevenLabs moved 0.114
against itself, Gemini 0.040 — and the model gap on that scenario was 0.207 in
one run and 0.053 in the next, i.e. the second run's gap was smaller than
ElevenLabs' own noise. The Models tab shows both numbers side by side: `Spread`
mixes noise with genuine between-scenario variation, `Repeat` is the noise
alone, and `Repeat` reads n/a until a scenario has been run twice rather than
reporting a reassuring 0.000 from a single sample.

The 0.5 tie band on Head-to-head is a fixed rule of thumb chosen before any run,
NOT a measured threshold. Where the two disagree, the Repeats tab is the one
with evidence behind it.

`run` prints an estimate and waits for `y` before spending. **Read the estimate.**

### Resume is free

An output that already exists is never regenerated and its transcript is never
re-purchased. A run that dies half way costs nothing to finish — re-run the
same command with `--run <same-id>`.

---

## 3 · Adding a TTS model

**One YAML block. No code**, unless the provider is new.

Open `configs/models.yaml`, copy an existing block under `voice:`:

```yaml
  - id: my-new-model              # our id; appears in reports and filenames
    enabled: true
    adapter: elevenlabs_tts       # which adapter class talks to it
    provider: elevenlabs          # LANE KEY — models sharing a vendor share a
                                  # semaphore, because quota is per vendor
    provider_model: "eleven_v3"   # the vendor's own model id
    auth_env: ELEVENLABS_API_KEY
    supports: [text_to_speech, styled_tts]
    limits: {max_concurrency: 2, rpm: 20}
    voice_map:
      female_mid_warm: "XrExE9yKIg1WjnnlVkGX"   # ONE deliberate voice per provider
    params: {format: wav, sample_rate: 24000}
    price:
      unit: per_1k_chars          # or tokens / per_1m_chars / per_minute
      usd: 0.10
      source: https://elevenlabs.io/pricing/api    # REQUIRED
      as_of: 2026-09-01                            # REQUIRED
```

Then just run. Nothing in `checks.py`, `judge.py`, `scoring.py` or `report.py`
knows a provider exists.

**Things that will stop you, on purpose:**

- A price block with no `source` or `as_of` is rejected at load. A rate with no
  provenance cannot be re-checked, and provider rates move.
- A scenario asking for a voice the model's `voice_map` does not declare is
  refused rather than guessed. An arbitrary voice is an undeclared difference
  between arms.
- `enabled: true` with a missing credential is a hard stop at preflight.
- Set `enabled: false` with a `disabled_reason:` rather than deleting a block —
  the block is the documentation of how to turn it back on.

### Adding a whole new provider

One adapter class in `runner/adapters/`, one line in `_REGISTRY` in
`runner/adapters/__init__.py`. Copy `openai_tts.py` — it is the shortest.
An adapter does exactly three things: translate our request into the provider's
shape, call the API once, return bytes plus raw usage. **No retries** (the
runner owns those), **no cost maths** (`cost.py` owns that), **no file writing**
(the runner owns that).

---

## 4 · Adding a scenario

One YAML in `scenarios/`. Everything a run needs is in it except the weights.

```yaml
id: voi-tel-11
modality: voice
task: text_to_speech          # text_to_speech | styled_tts — SELECTS THE RUBRIC
title: "Short human-readable title"

input:
  script: |
    The exact words to speak. Sent byte-for-byte to every model.
  language: en-IN
  style: "..."                # presence of a style directive implies styled_tts

params:
  voice: female_mid_warm      # logical name; mapped per provider in models.yaml
  format: wav
  sample_rate: 24000

expected: |
  Plain English. Goes into the JUDGE prompt verbatim.

checks:                       # deterministic. Code decides these; judge never sees them.
  duration_s: {min: 6.0, max: 30.0}
  max_silence_s: 2.5
  no_clipping: true
  max_wer: 0.10               # normalized WER
  must_say_digits: "4419"     # optional: digit-exact readback
  max_digit_run: 4            # optional: security — no digit run longer than N
  must_say:                   # optional: required phrases, spelled AS SPOKEN
    - "four thousand two hundred and fifty rupees and seventy-five paise"
                              # NOT "4250.75 rupees" — that normalizes to a
                              # decimal reading and failed two correct clips
  wer_reference: |            # optional: negative control — measure against
    deliberately different text                # something other than the script

  # --- added 2026-09-03 for the real-use-case bank ---
  must_not_say:               # optional: phrases that must be ABSENT. The
    - "two hundred fifty thousand"   # check must_say cannot express — a true
                              # statement in the wrong convention, or a
                              # disclosure whose failure is a phrase PRESENT
  trimmed_duration_s:         # optional: length with lead/trail silence
    {min: 14.9, max: 15.1}    # removed — an ad slot, a shot. Gating the RAW
                              # length would let a model pass by padding
  speech_rate_wpm:            # optional: delivery pace, from the SCRIPT's
    {min: 190.0, max: 280.0}  # word count over the trimmed read
  rms_dbfs: {min: -23.0, max: -18.0}   # optional: mastering spec (ACX)
  peak_dbfs_max: -3.0                  # optional: mastering spec (ACX)
  noise_floor_dbfs_max: -60.0          # optional: room tone (ACX). UNMEASURED,
                              # never a pass, when there is under 0.2s of
                              # lead/trail silence to measure it in

tags: [telephony, digits]
```

**`must_say_digits` only works at four digits or more.** Below that a number
normalizes to a cardinal — "fourteen", not "one four" — and the extractor
cannot see it. Use `must_say` with a phrase instead. This is enforced:
`tests/test_scenario_bank.py` replays every digit gate against the script
rewritten the way an ASR writes numbers, which is how a real scenario was
caught asserting a score no correct reading could have satisfied.

CSV works too, for bulk entry by non-developers — `id, task, script, style,
language, expected, max_wer`, one row per scenario. Same object comes out.
The optional per-scenario checks above are YAML-only.

**A failed check is decisive:** score 0, marked `invalid`, and the judge is
never called — so broken output never costs judge money.

---

## 5 · The three config layers

```
scenarios/*.yaml     what to say + what must be true of THIS output
configs/rubrics/     how criteria are weighted, per TASK
configs/models.yaml  who says it, at what price, with which voice
```

Nothing in a scenario names a model. Nothing in the model config knows a
scenario exists. That is what makes "same input to every model" structurally
true rather than a promise.

### Rubrics

`configs/rubrics/voice.yaml` is the base — five criteria summing to 1.0.
`configs/rubrics/tasks/styled_tts.yaml` overrides it for styled scenarios,
adding `style_adherence` and taking weight back from the others.

The load-bearing field is `scored_by`:

- `measurement` — computed from a measurement. **The judge never sees it**; the
  value is injected into its prompt as an established fact instead.
- `judge` — the judge scores it. Code has no opinion.
- `hybrid` — both, blended at the declared ratio.

Change a criterion to `measurement` and the judge stops being asked about it.
No code edit.

**Editing a weight re-scores every past run for free** — criterion scores are
stored per cell, so `report` recomputes from them. No regeneration, no
re-judging, no spend.

**Known gap:** a scenario may declare its own `criteria:`/`weights:`. They are
parsed and validated and then **ignored** — rubrics resolve on `task` only.
Either wire it through or delete the fields; do not leave it as it is.

---

## 6 · What a run leaves on disk

```
runs/<run-id>/            one run = ONE scenario x every model
├── manifest.json     git sha, model ids, prices + source + as_of, rubric
│                     hashes, scenario-set hash, pinned voices, budget
├── summary.json      DERIVED rollup for dashboards — rebuild with `summarise`
├── scenarios/        frozen copy of the scenarios THIS run used
├── outputs/voice/<scenario>/<model>.wav   the audio
│                        └── <model>.txt   the raw ASR transcript, as evidence
├── telemetry.jsonl   one row per attempt, including failures
├── checks.jsonl      every gate + the normalized WER pair, replayable by eye
├── judge.jsonl       reasoning, scores, prompt hash, blind map
├── scores.jsonl      weighted totals + effective weights
└── report.html       opens from disk, no server
```

Everything except `summary.json` and `scores.jsonl` is **append-only**. A
correction is a new run. Zip the folder and it plays on anyone's machine.

---

## 7 · Rules the code enforces, and why

Read these before changing scoring — each exists because the alternative
produced a wrong number.

- **Unjudged is not zero.** A judge failure is its own state, excluded from the
  mean, counted separately. Averaging it as 0 declares whichever model the
  judge choked on to be the worst.
- **Invalid IS zero.** A clip that failed a gate earned it — the model produced
  something and it was unusable.
- **A scenario is complete only when every model answered it.** One arm alone
  is not comparable. Partial scenarios are announced INCOMPLETE and named in
  `summary.json`.
- **Quality, cost, latency and reliability are never blended.** Four columns, a
  human decides.
- **A winner needs a 0.5 mean gap OR 70% of decided scenarios**, else it is a
  declared tie. These models are non-deterministic; a 0.3 lead is noise.
- **Estimated costs are labelled** and stay labelled into the report.
- **The signal audio-quality metric is not a MOS** and is badged as such
  everywhere. Do not remove that badge without swapping in a real predictor.
- **The judge is uncalibrated** until two people score five clips. Every quality
  figure carries the badge until then.

---

## 8 · Open items when I left

| | What | Where |
|---|---|---|
| Judge protocol | Pointwise scoring compresses — both models scored 10/10 on most criteria. Pairwise/CMOS is the fix; research is done, not built. | `runner/judge.py` |
| Audio quality | Signal heuristic, not a MOS. UTMOSv2 is the right swap (`pip install`, local, free). **Not** DNSMOS — it is denoise-tuned and worse on TTS. | `runner/mos.py`, `configs/models.yaml` `mos:` |
| Calibration | Never run. Needs 5 genuinely different clips; one narration passage read six times cannot be ranked. | `runner/calibration.py` |
| Scenario rubric overrides | Parsed, validated, ignored. | `runner/scenarios.py` |
| Rubric axis | Ours keys on `task`; the team's spreadsheet keys on **use-case family** with different weights per family (A4 ×3 for transactional). Not aligned. | `configs/rubrics/` |
| Drift checks | VOI-NAR-02 wants minute-1 vs minute-5 naturalness and a speaker-embedding cosine. Neither exists. | `demo/scenarios/` |
| Normalizer | Handles digits, currency, thousands separators, Roman numerals, abbreviations, UK/US spelling. Does **not** handle letter-by-letter spelling (email/booking-code scenarios). | `runner/normalize.py` |

---

## 9 · When something breaks

**"Hard stop: an enabled model has no credential"** — the named variable is not
in `.env`. Set it, or `enabled: false` the model.

**A cell hangs** — it cannot for long: the runner owns a wall-clock deadline per
attempt and abandons a stuck call rather than letting one cell block the run.
If you see `exceeded the runner's Ns wall-clock deadline`, the hang was below
the SDK (usually an auth token refresh).

**WER looks absurdly high on a correct clip** — almost always a normalization
gap, not a model failure. `checks.jsonl` stores both normalized strings; read
them side by side before blaming the model.

**Costs look wrong** — check `usage_exact` in `telemetry.jsonl`. `false` means
the provider returned no usage and the cost came from declared assumptions in
`models.yaml`, which are visible and editable.
