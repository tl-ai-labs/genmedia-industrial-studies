# voice — the TTS comparison lane

Local runner for the GenMedia Model Comparison Plan v1.2. Same script to every
TTS model, outputs and telemetry on disk, code measures everything measurable,
a blind judge scores only what code cannot, and quality / cost / latency /
reliability are reported as four separate numbers — never blended into one.

Files and JSONL. No server, no queue, no database, no Docker.

```bash
uv venv --python 3.12 .venv
VIRTUAL_ENV=$PWD/.venv uv pip install -e '.[google,dev]'    # or '.[openai,dev]'

.venv/bin/python -m runner.cli --modality voice run    --budget 2.00 --yes
.venv/bin/python -m runner.cli --modality voice judge
.venv/bin/python -m runner.cli --modality voice report --open
.venv/bin/python -m runner.cli --modality voice dashboard --open   # across ALL runs
```

`run`, `judge` and `report` are separate commands on purpose: re-judging must
never re-generate audio, and a report tweak must cost nothing.

`report` renders ONE run — its clips, gates and judge reasoning — and is what
makes a run folder self-contained enough to zip and mail. `dashboard` renders
`runs/index.html` ACROSS every run, which is where run-to-run spread becomes
visible; a single run cannot show you how much a number moves, and that is
usually the first thing worth knowing.

**Voice runs as its own process.** `--modality voice` filters the scenarios,
the models and the output directory, so it can run at the same time as an
image process and the two never touch each other's files.

---

## 1 · Adding a TTS provider

One YAML block, one adapter class. Nothing in `checks.py`, `judge.py`,
`scoring.py`, `cost.py` or `report.py` changes, and nothing in them knows a
provider exists.

**The block** (`configs/models.yaml`):

```yaml
voice:
  - id: my-tts                       # appears in filenames, telemetry, report
    enabled: true
    adapter: my_tts                  # -> runner/adapters/my_tts.py
    provider: myvendor               # semaphore key: quota is per vendor
    provider_model: "voice-model-1"
    auth_env: MY_TTS_API_KEY         # checked at preflight, before any spend
    supports: [text_to_speech, styled_tts]
    limits: {max_concurrency: 2, rpm: 20}
    voice_map: {female_mid_warm: "their-voice-id"}
    params: {format: wav, sample_rate: 24000}
    price: {unit: per_1k_chars, usd: 0.05, source: <url>, as_of: 2026-09-01}
```

**The class** (`runner/adapters/my_tts.py`) implements one method:

```python
class MyTtsAdapter(BaseAdapter):
    ext = "wav"
    def run(self, req: GenRequest) -> GenResult:
        ...
        return GenResult(data=audio_bytes, mime="audio/wav",
                         provider_version=..., usage=Usage(...),
                         applied_params={...})
```

Then one line in `runner/adapters/__init__.py`'s `_REGISTRY`.

Adapters do exactly three things: translate the request into the provider's
shape, call the API once, and return bytes plus raw usage. They do **not**
retry (the runner owns that), do **not** do cost maths (`cost.py` owns that),
and do **not** write files (the runner owns that).

Four adapters ship: `gemini_tts`, `openai_tts`, `elevenlabs_tts`, and the ASR /
judge backends. Which arms are live is a `enabled:` boolean, not a code change.

### Billing differs per provider, and is handled per provider

| unit | who bills this way | priced from |
|---|---|---|
| `tokens` | Gemini TTS, gpt-4o-mini-tts | provider's reported usage; falls back to **declared** assumptions in the price block, labelled `est` |
| `per_1k_chars` | ElevenLabs | characters **sent** — exactly what the vendor bills |
| `per_1m_chars` | OpenAI tts-1 | characters sent |
| `per_minute` | per-minute ASR | the **measured** duration of the written file, never the requested one |

Every cost is integer micro-USD. Every rate carries a `source` and an `as_of`.
Every cost record carries `usage_source` (`api_reported` / `estimated`) **and**
`usage_exact`, because a character-billed call is exact without being
API-reported — and the report badges on exactness, so a number that needs no
discount does not get one.

---

## 2 · What code measures, and what the judge is allowed to touch

Layer 1 and 2 are free, deterministic, and run **before** any judge call. A
clip that fails a gate never reaches a paid judge.

| | tool | produces |
|---|---|---|
| decode, duration, silence, clipping, loudness | `soundfile` + `numpy` | gates + measurements |
| transcript | ASR (`gemini_transcribe` or `openai_transcribe`) | `<model>.txt` beside the audio |
| word accuracy | `jiwer` over the normalized pair | `normalized_wer` |
| number/digit normalization | `num2words` | see below |
| objective audio quality | local predictor, CPU, free | `quality_1_5` |

**The MOS predictor is pluggable and labelled.** `mos.predictor: dnsmos` loads
a DNSMOS/NISQA ONNX from `mos.model_path` and reports a genuine MOS.
`mos.predictor: signal` (the default, because no ONNX is vendored) computes
SNR, spectral flatness, clipping ratio, DC offset and bandwidth and maps them
onto the same 1–5 axis. It carries `is_mos: false`, and every surface —
telemetry, judge prompt, report — says *not a MOS*. To upgrade: drop the ONNX
in and change one word in `configs/models.yaml`.

**The judge scores naturalness, pronunciation, clarity and artefacts. Nothing
else.** Word accuracy is measured and injected as an established fact with an
explicit instruction not to re-score it.

### The WER normalization pipeline

One shared function, `runner/normalize.py:normalize()`, applied to **both** the
script and the transcript:

```
lowercase → strip punctuation → expand digits/numbers → collapse whitespace
```

Order matters. Punctuation is stripped *before* number expansion, so
`4-8-2-9-1-6` has already become six single-digit tokens by the time the
expander sees it — which is what makes it converge with the bare `482916`
spelling that the expander splits digit-by-digit on its own. Digit runs of 4+
(or with a leading zero) are identifiers and go digit-by-digit; shorter runs
are quantities and become cardinals.

The gate and the score use the normalized WER only. The **raw** transcript is
kept on disk as evidence and never edited; the normalized pair and the
resulting WER go into `checks.jsonl` so a disputed gate can be replayed by eye,
and the report renders the word-level diff.

`tests/test_normalize_and_wer.py` proves both directions: it catches a
deliberately wrong script, and it passes a digits-heavy correct one that raw
WER would have failed on formatting alone.

---

## 3 · How rubrics are calculated

Criteria and weights live in `configs/rubrics/voice.yaml`, per-task overrides
in `configs/rubrics/tasks/<task>.yaml`. **Nothing is hardcoded in Python.**

```yaml
criteria:
  - {key: text_accuracy, weight: 0.30, scored_by: measurement, measurement: normalized_wer, scale: ...}
  - {key: pronunciation, weight: 0.20, scored_by: judge}
  - {key: naturalness,   weight: 0.20, scored_by: judge, calibration_gated: true}
  - {key: clarity,       weight: 0.15, scored_by: judge, calibration_gated: true}
  - {key: audio_quality, weight: 0.15, scored_by: hybrid, measurement: mos,
                         blend: {measurement: 0.5, judge: 0.5}}
```

**`scored_by` is the load-bearing field.** `judge.py` builds its criteria list
from `scored_by in (judge, hybrid)` only. Change `pronunciation` to
`measurement` and the judge stops being asked about it — no code edit.

End to end, for one cell:

1. **Load.** Base rubric + task override merged. Weights must sum to `1.0` or
   the loader **rejects** — a normalised typo is a scoring bug that never
   announces itself.
2. **Hash.** sha256 over the *merged* rubric, attributes sorted so key order
   cannot change it. Stamped on every `scores.jsonl` row, every judge record
   and the manifest.
3. **Measured criteria → score.** `normalized_wer` through the declared scale
   (WER 0 → 10, 0.10 → 5, ≥0.20 → 0). `quality_1_5` through a 1–5 → 0–10 map.
4. **Judged criteria → score.** From the blind judge's structured JSON.
5. **Hybrid.** `0.5 × measured + 0.5 × judged`, ratio declared in the rubric.
6. **Weighted total.** `Σ(weight × score)` → 0–10.
7. **Unmeasured criteria redistribute.** ASR failed? `text_accuracy` is
   dropped and its weight spread proportionally over the survivors. It never
   becomes a 0 — that would punish the model for our measurement failing.
8. **Three states.** `scored` · `invalid` (failed a gate → an earned 0) ·
   `unjudged` (no measurement → a dash, excluded from the mean, counted
   separately). Nothing missing ever becomes a number.

Re-scoring after a weight edit costs nothing: `report` recomputes from stored
criterion scores. No regeneration, no re-judging.

### The verdict — two lenses

```
A beats B  ⇔  mean(A) − mean(B) ≥ 0.5
           OR A wins ≥ 70% of the DECIDED scenarios (tie band |Δ| ≤ 0.5)
           backed by a sign test at ≥ 10 decided
```

LLM judges compress into a narrow band, so the mean under-reports real
differences; the paired win count survives that because both models answered
the identical scenario. Neither lens alone. A winner also needs 80% coverage.

### The calibration gate

Naturalness and prosody are the least-validated link in the chain, so they are
**not trusted** until two people score five clips and agree with the judge
(mean |human − judge| ≤ 1.0, rank correlation ≥ 0.9). Until then the report
shows a **`judge uncalibrated`** badge on the quality column and every verdict
is marked PROVISIONAL.

```bash
.venv/bin/python -m runner.cli calibrate --init      # writes calibration/voice.yaml
# two people fill it in, then:
.venv/bin/python -m runner.cli report
```

Calibration is bound to the rubric hash and the judge model. Edit a weight and
the gate reverts to uncalibrated, because the thing that was validated no
longer exists.

---

## 4 · Run folder

Self-contained and zippable. Nothing under `outputs/` is ever rewritten; a
correction is a new run.

```
runs/<run-id>/
├── manifest.json          run id, git sha, model + voice pins, rubric hashes,
│                          prices, budget, scenario set hash, skipped cells
├── scenarios/             frozen copy of every scenario this run used
├── outputs/voice/
│   ├── voi-001/
│   │   ├── gemini-2-5-flash-tts.wav      the artefact
│   │   ├── gemini-2-5-flash-tts.txt      its RAW ASR transcript, as evidence
│   │   ├── gemini-2-5-pro-tts.wav
│   │   └── gemini-2-5-pro-tts.txt
│   └── voi-002/ …
├── telemetry.jsonl        one row per ATTEMPT, including failures
├── checks.jsonl           gates + measurements + the normalized WER pair
├── judge.jsonl            reasoning, scores, prompt hash, blind map
├── scores.jsonl           one row per scenario × model
└── report.html            open it straight from the folder
```

Scenario → model, not stage → seed: a scenario's folder is a side-by-side of
every model that answered it, which is the shape the report reads and the
shape a human browsing the folder expects.

---

## 5 · Feeding in scenarios

Two formats, one object. Nothing downstream knows which was used.

**YAML** (`scenarios/*.yaml`) — full fidelity:

```yaml
id: voi-002
modality: voice
task: text_to_speech
input:
  script: |
    Hello, this is Trailhead confirming order 4-8-2-9-1-6.
  language: en-US
  style: "warm, apologetic, unhurried"      # styled_tts only
params: {voice: female_mid_warm, format: wav, sample_rate: 24000}
expected: |
  Order number read digit by digit, no digit dropped or merged.
checks:
  duration_s: {min: 8, max: 32}
  max_silence_s: 2.0
  no_clipping: true
  max_wer: 0.10
```

**CSV** (`scenarios/*.csv`) — one row per scenario, for a spreadsheet:

```csv
id,task,script,style,language,expected,max_wer
voi-010,text_to_speech,"Your appointment is confirmed for Tuesday at 2:15 PM.",,en-IN,"Time is unmistakable.",0.10
voi-011,styled_tts,"Only 3 seats left. Book before midnight.","energetic, upbeat",en-US,"Reads as an ad without overacting.",0.12
```

Scripts are never hardcoded. A missing column, an empty script, a duplicate id
or weights that do not sum to 1.0 are errors naming the file and row.

`checks.wer_reference` is a negative-control affordance: it measures WER
against text that deliberately differs from what was spoken, so a run can
prove its own gate still says no. Ordinary scenarios omit it.

---

## 6 · Honesty rules the code enforces

- **A missing key is a hard stop before any spend**, not a failure on call 14.
- **A failed call is a recorded failure** with its own status
  (`rate_limited` / `timeout` / `refused` / `quota_exhausted` / `provider_error`),
  never a silent gap and never a 0.
- **Judge failure is `unjudged`**, excluded from the mean, shown as a dash.
- **A gate failure is an earned 0** — the model produced something unusable.
- **An unsupported task is `n/a`**, excluded from every denominator.
- **Retry ×3** with provider-scoped backoff and jitter; a non-retryable error
  (auth, refusal, exhausted quota) stops immediately rather than burning three
  attempts.
- **Resume is free.** An existing output is never regenerated and an existing
  transcript is never re-purchased.
- **The script is passed by value.** Nothing appends to it, including the
  retry path.
- **Per-provider semaphores + optional rpm pacing**, so the reliability column
  measures the provider and not our own hammering.

## 7 · Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

80 tests, fully offline — no API key, no network. Scoring, cost maths, gates,
normalization, rubric loading and judge blinding are all pure functions or run
against audio the test wrote itself.
