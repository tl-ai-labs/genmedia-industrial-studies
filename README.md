# genmedia-industrial-studies

Industrial GenMedia model comparisons — the same standard applied to every
modality (image, video and voice): the same input goes to every model,
automatic pass/fail checks run before any AI scoring, a blind AI judge scores
what those checks can't, and **quality, cost, latency and reliability are
reported as four separate numbers — never mixed into one score**.

Implements the *GenMedia Model Comparison Plan v1.2* (31 Aug 2026). This
README is the map of the whole repo. Each lane then documents itself in
depth — follow the links as you go deeper.

**Contents:** [Overview](#overview) · [Models](#models) ·
[Scenarios](#scenarios) · [Folder structure](#folder-structure) ·
[Running a lane](#running-a-lane) · [Troubleshooting](#troubleshooting) ·
[The cell lifecycle](#the-cell-lifecycle) ·
[Honesty rules](#honesty-rules) · [Further reading](#further-reading)

## Overview

One evaluation engine, three independent lanes, plus one tool that reads
across all of them. A lane never imports another lane's code — each is its
own Python package with its own virtual environment, scenarios, models,
adapters, tests and `runs/`.

One term used a lot below: an **arm** just means one model being compared —
"one side" of the comparison, like one arm of a trial.

### The three lanes

| Lane | Status | What it compares | Start here |
|------|--------|-------------------|------------|
| [`image/`](image/README.md) | **complete** | Text-to-image + image editing — Gemini 3 Pro / Flash vs GPT Image 2, blind-judged. 154 scenarios across the bank | [image/README.md](image/README.md) |
| [`video/`](video/README.md) | **in progress** | Text-to-video, cinematic + physics families (20-scenario v1 bank; 60 extracted, 40 pending assets). Active arms: Omni Flash vs Seedance 2.5; Veo 3.1 / Sora 2 parked | [video/README.md](video/README.md) |
| [`voice/`](voice/README.md) | **built, bank partial** | TTS / styled speech — Gemini 3.1 Flash TTS vs ElevenLabs v3 — deterministic audio gates, ASR-verified WER, blind-judged. 18 scenarios runnable, 11 blocked pending instrumentation | [voice/README.md](voice/README.md), [voice/HANDOFF.md](voice/HANDOFF.md) |

**`panel/` is not a fourth lane — it's the cross-lane tool that reads the
other three.** It never generates or judges anything itself: it takes runs
from image/video/voice, shows a reviewer two outputs with the model names
hidden, records which one they picked, and reports how often the human
majority agrees with each lane's own judge. Status: **built, early votes**.
Start here: [panel/README.md](panel/README.md).

One name collision worth knowing up front, since it's the single most
disorienting thing about this repo for a newcomer: **`voice/panel/` is that
lane's own internal review tooling — a different, older thing — and is
unrelated to this root-level `panel/`.**

Shared across the three lanes:

- [`shared/report_kit/`](shared/report_kit) — the **one presentation layer**
  for every report: layout, sections, styling, number formats and file
  names. Lanes supply data and media only; a formatting change is made once
  here and image, video and voice all pick it up. Rules for changing it:
  [shared/report_kit/RULES.md](shared/report_kit/RULES.md).
- [`assets/`](assets) — the master scenario-bank workbook
  (`genmedia_validation_rubrics_with_industry_mappings.xlsx`: scenarios,
  rubrics, industry mappings) that `image/` and `video/` scenarios are
  extracted from. `voice/`'s bank was sourced from a separate,
  voice-specific workbook — see [voice/HANDOFF.md](voice/HANDOFF.md) §6.
- **Evaluation standard**: deterministic gates → measurements → blind AI
  judge → weighted score. Rubric weights must sum to 1.0 — the loader
  rejects anything else rather than silently normalising. A failed call is a
  recorded failure, never a silent gap. Runs are immutable; a correction is
  a new run.
- **Run folder shape**: every run is a self-contained, mostly-zippable
  folder at `<lane>/runs/<run-id>/` — see
  [What a run produces](#what-a-run-produces) below.

## Models

Every model lives in one place per lane — `<lane>/configs/models.yaml` — as
a block with `enabled: true/false`, the route it's served from, and a price
that notes where it came from and when it was checked. Turning a model on
or off is a one-line config change; nothing else in the codebase needs to
change. The tables below don't track which models are enabled right now —
that's a detail the runner checks for itself at run time, and a missing key
or a disabled model stops cleanly before anything is spent.

### image — Gemini vs GPT Image

All of these are usable — nothing here needs to be flipped on first; the
runner checks keys and quota for itself when you actually start a run.

| Model | Vendor | Route | Price |
|---|---|---|---|
| **Gemini 3.1 Flash Lite Image** ("Nano Banana Lite") | Google | Vertex AI | $0.0336 / image |
| **GPT Image 2 (low)** | OpenAI | direct API | ≈$0.02 / image (est.) |
| Gemini 3 Pro Image ("Nano Banana Pro") | Google | Vertex AI | $0.134 / image |
| Gemini 3.1 Flash Image | Google | Vertex AI* | $0.067 / image |
| GPT Image 2 (medium / high) | OpenAI | direct API | ≈$0.05–$0.20 / image (est.) |
| GPT Image 1 | OpenAI | direct API | ≈$0.07 / image (est.) |

`*` — a direct-API route is also configured for this model
(`gemini-3-1-flash-image`), but it stays disabled: the API key behind it is
on the free tier, which has zero image quota. Vertex is the route that
actually works, billed to the team's paid GCP project instead.

`(est.)` — OpenAI bills by token, not per image; the figure shown is a
pre-flight planning estimate, and the actual billed cost per image (from
returned usage) can differ. Gemini's prices are exact — Google bills a fixed
rate per image.

Prices verified against `configs/models.yaml`: 2026-09-22.

Judge: **Gemini 3 Flash** (Vertex AI), temperature 0 (no randomness — the
same input always scores the same), ≈$0.003/call.

### video — Omni Flash vs Seedance 2.5

| Model | Vendor | Route | Price |
|---|---|---|---|
| **Omni Flash** (`gemini-omni-1.1-flash-preview`) | Google | Vertex AI (Interactions API) | ≈$0.81 / clip (720p) – $1.22 / clip (1080p), measured |
| **Seedance 2.5** (`dreamina-seedance-2-5-260628`) | ByteDance | BytePlus ModelArk | ≈$4.18 / clip (1080p, audio on), measured |

`measured` — both bill by token, so cost varies with the clip's actual
content; the figures shown are pulled from real completed calls (not a
vendor quote), but will still drift from a specific future clip. Seedance's
number is the current run's real configuration (1080p, audio on) — an older
$4.55 list-price figure and an older $1.87 720p measurement are both
superseded by this one, per the dated note in `configs/models.yaml`.

Prices verified against `configs/models.yaml`: 2026-09-22.

Judge: **Gemini 3 Flash** (Vertex AI) — the same judge model as the image
lane; it reads mp4 media natively.

### voice — Gemini 3.1 Flash TTS vs ElevenLabs v3

| Model | Vendor | Route | Price |
|---|---|---|---|
| **Gemini 3.1 Flash TTS** (preview) | Google | Vertex AI, `us-central1` | $1 / 1M input tokens, $20 / 1M audio-output tokens |
| **ElevenLabs v3** (`eleven_v3`) | ElevenLabs | direct API, US region | $0.10 / 1k characters sent |
| ElevenLabs Multilingual v2 — from past runs, since replaced by v3 above | ElevenLabs | direct API | $0.10 / 1k characters |

Voice has no fixed "per clip" price — clip length varies a lot by scenario,
so Gemini's token rates and ElevenLabs' character rates are each shown as
their real billing unit rather than forced into one shape. For a feel of
the real dollar amounts: a **measured** run put Gemini at **≈$0.0069/clip
($0.0430/audio-minute)** and ElevenLabs at **≈$0.0144/clip
($0.0991/audio-minute)** (`voice/HANDOFF.md` §13, 2026-09-04). That
ElevenLabs figure is against **v2** — the character rate carried over
unchanged into v3 ($0.10/1k either way), so it's a reasonable proxy, but v3
has not been separately measured for $/clip.

Prices verified against `configs/models.yaml`: 2026-09-22.

Judge: **Gemini 2.5 Flash** (Vertex AI, `us-central1`), listening to the
audio directly. **Disclosed caveat**: this is a Google model judging a
comparison that includes a Google arm, on the same vendor and region — the
project has accepted this deliberately (footnoted on every report) rather
than hide it, and a neutral-judge cross-check is the highest-value
experiment left undone (see [voice/HANDOFF.md](voice/HANDOFF.md) §9).

The ASR (Automatic Speech Recognition — it turns each clip back into text)
is what every WER (word error rate) and word-accuracy gate is measured
against: **local Whisper `medium`** (`faster-whisper`, runs on CPU, free, no
key, no network) — deliberately *not* a Google model. An earlier Gemini-based
ASR was retired after it was found to measurably favour the Gemini TTS arm
purely by sharing a vendor (median WER gap dropped from +0.0103 to +0.0001
once a neutral recogniser listened) — see [voice/HANDOFF.md](voice/HANDOFF.md) §8.

### Adding a model or provider

The same pattern in every lane:

1. **One block** in `configs/models.yaml`: `id`, `enabled: true`, `adapter`,
   `auth_env`, `supports`, `limits`, and a sourced + dated `price`.
2. **One adapter file** in `runner/adapters/` (or `runner/video/`) exposing a
   single method that translates the request, calls the API once, and
   returns bytes + raw usage. Adapters never retry, never do cost maths,
   never write files — the runner owns all of that.
3. Register the adapter in one line (voice: `_REGISTRY` in
   `runner/adapters/__init__.py`; image/video: same pattern).

Nothing in `checks.py`, `judge.py`, `scoring.py`, `cost.py` or `report.py`
changes, and none of them know a specific provider exists.

## Scenarios

A scenario is the input to one row of a comparison: a brief every enabled
model answers identically, plus the checks its answer must pass. Scenarios
are never organised by model — every enabled model answers every scenario,
so a scenario can't "belong" to one. They're organised by **use-case
family**: folder and id prefix group scenarios by task (image's
`bank-edit/IMG-BRAND-*`, video's `bank-video/VID-CIN-*`, voice's
`vr-ecom-*`/`vr-drama-*`/...). **Industry is a separate tag**, layered on
top via each lane's `configs/industry_map.yaml` (a scenario id → industry
lookup, e.g. `IMG-BRAND-01` maps to "Ecommerce & Retail"), not the folder
structure — one scenario can carry more than one industry tag.

### Where scenarios live, and in what form

Put a new scenario in the lane's `scenarios/` directory — it's picked up
automatically, there's no registry to edit. Three formats exist; nothing
downstream cares which one was used:

- **YAML** — one file, one scenario, full detail. Every lane accepts this,
  and it's the default format to write by hand.
- **CSV** — one row per scenario, for a batch exported from a spreadsheet.
  Every lane accepts a CSV passed to `run --scenarios <file>.csv`.
- **XLSX** — a workbook with a sheet named `scenarios` that loads as a full
  batch, the same way a CSV does (e.g. `image/scenarios/batches/image-v1.xlsx`,
  `video/scenarios/batches/video-v1.xlsx`). **Image and video only** —
  voice's loader reads YAML and CSV, not XLSX.

Don't confuse a lane's own `scenarios/batches/*.xlsx` with the **master**
workbook in [`assets/`](assets) — the master is the human-edited source
scenarios were originally extracted from (rubrics, industry mappings
included); the lane-local one is a derived, loader-ready copy the CLI
actually reads.

The exact fields differ by modality — full spec and worked examples are in
each lane's README ([image/README.md](image/README.md) § "Adding a
model/provider/scenario", [video/README.md](video/README.md) § "Scope (v1)",
[voice/README.md](voice/README.md) § "5 · Feeding in scenarios") — but the
shape is always:

```yaml
id: img-011-example            # unique; the family prefix drives grouping
modality: image                # image | video | voice
task: text_to_image            # e.g. text_to_image, text_to_video, text_to_speech
prompt: |                      # voice/video call this field "input" instead
  the exact brief every model receives, verbatim
params: {size: "1024x1024"}    # e.g. size, aspect_ratio, voice, language
expected: |                    # plain English, read by both the judge and a human
  what a correct output looks like
checks:                        # HARD, deterministic gates — a failure is an earned 0
  not_blank: true
criteria: [prompt_adherence, visual_quality]     # or leave to the lane's rubric default
weights: {prompt_adherence: 0.6, visual_quality: 0.4}
tags: [example]
```

(This is a real, valid YAML file you could save and run as-is — copy an
actual scenario from a lane's `scenarios/` directory as your starting
point rather than this skeleton, though; it's illustrative, not a template
meant to be filled in.)

### Rules that hold in every lane

- **Every gate must be passable by a perfect output.** A gate no correct
  answer can satisfy measures the harness, not the model — every lane has
  been burned by this at least once (see voice/HANDOFF.md §8 for the list).
- **Scripts/prompts are never hardcoded in Python.** They live only in the
  scenario file.
- **A scenario that cannot yet be reliably checked goes in a `blocked/`
  folder, never `scenarios/`.** Anything under `scenarios/` runs and spends
  money; voice's `blocked/scenarios/` is the working example.

Run the lane's offline scenario-bank test after adding one (e.g.
`pytest tests/test_scenario_bank.py`, where present) before spending
anything on it.

## Folder structure

```
genmedia-industrial-studies/
├── image/    text-to-image + edit lane (complete)
├── video/    text-to-video lane (in progress)
├── voice/    text-to-speech lane (built, bank partial)
├── panel/    cross-lane blind human panel + judge correlation
├── shared/   the report-rendering kit every lane's `report.py` calls into
├── assets/   the shared scenario-bank workbook
└── docs/     dated planning notes and review write-ups
```

Each of the three comparison lanes (image/video/voice) shares this core —
it's the common shape, not the full listing. Voice in particular carries
several extra lane-specific directories beyond it (calibration data, a
client-facing dashboard export, blocked scenarios, its own internal
`panel/`...); see that lane's own README for its complete layout.

```
<lane>/
├── README.md          how this lane works — read this before touching it
├── .env.example        which keys/credentials this lane needs, and why
├── pyproject.toml      its own, independent Python package
├── configs/
│   ├── models.yaml      every model: enabled?, adapter, auth env var, price
│   ├── rubrics/         scoring criteria + weights (never hardcoded)
│   └── industry_map.yaml
├── scenarios/          the inputs a run reads — see Scenarios above
├── runner/              the engine: cli.py, adapters/, checks.py, judge.py,
│                        scoring.py, report.py …
├── runs/                one folder per run — the evidence (see below)
└── tests/               offline, no keys, no network, no spend
```

`panel/`'s own layout is different — it's a tool, not a lane — with
`site/`, `deploy/`, a `Dockerfile` and `votes.jsonl` instead of the above;
see [panel/README.md](panel/README.md).

## Running a lane

### Prerequisites

- **Python 3.11 or newer** — every lane's `pyproject.toml` pins `>=3.11`.
  The commands below assume a `python3.12` binary is on `PATH`; use whatever
  3.11+ interpreter you have if it's named differently.
- **API keys / credentials**, only for the lanes and models you actually
  enable:
  - `GEMINI_API_KEY` / `GOOGLE_APPLICATION_CREDENTIALS` (Vertex ADC —
    Application Default Credentials) for any Gemini/Vertex arm, the judge,
    and (voice) the ASR.
  - `OPENAI_API_KEY` for GPT Image / Sora / OpenAI TTS arms.
  - `ELEVENLABS_API_KEY` for the ElevenLabs voice arm.
  - `ARK_API_KEY` for the Seedance video arm (BytePlus ModelArk).
  - Vertex-backed arms additionally need
    `gcloud auth application-default login` on an account with Vertex access
    to the project named in that lane's `configs/models.yaml`.
- **Nothing spends without a key.** A missing key for an `enabled: true`
  model is a hard stop before any call is made — this is enforced in code,
  every lane, on purpose.
- `panel/` needs none of the above — it only reads other lanes' `runs/`
  folders and is offline by design.

Each lane has its own virtual environment. Pick the lane, `cd` into it, set
it up once, then use its three-verb pipeline: **run** (spends money,
generates outputs) → **judge** (spends money, scores them) → **report**
(free, renders `report.html`). Re-judging never re-generates; re-reporting
never re-judges.

Below, each lane is split into a **dry run** (free — proves your setup
works before anything can spend) and a **real run** (spends money). There is
no dedicated `--dry-run` flag anywhere in this repo — the free step is
always the test suite, which runs fully offline. For video specifically,
even its `run` command is free at first, because every model in that lane
ships disabled by default; there's nothing to spend on until a human enables
one.

Wherever you see `<run-id>` below, that's a placeholder, not something to
type literally. `run` prints it to the screen when it finishes — along with
the exact next command to run — and it's also the folder name `run` creates
under `runs/`, so you can always find it there too. **Voice is the one
exception**: if you leave `--run` off `judge`/`report`, it automatically uses
the most recent run, so you don't need to copy anything.

### image

**Dry run** (free — no key required to get this far):

```bash
cd image
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env                # fill in GEMINI_API_KEY, OPENAI_API_KEY
.venv/bin/python -m pytest          # offline, no keys, no spend
```

**Real run** (spends money once the pre-flight budget check clears — two
models ship enabled):

```bash
.venv/bin/python -m runner.cli run    --modality image --scenarios scenarios/ --budget 5.00
.venv/bin/python -m runner.cli judge  --run <run-id>
.venv/bin/python -m runner.cli report --run <run-id> --open
.venv/bin/python -m runner.cli cost   --run <run-id>
```

`--budget` is a hard ceiling, not a preview — the pre-flight cost estimate
`run` prints before it commits is the closest thing to a preview you get.

### video

**Dry run** (free, and genuinely safe — every model ships disabled):

```bash
cd video
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env                # OPENAI_API_KEY / ARK_API_KEY as needed;
                                     # Gemini/Veo route via `gcloud auth application-default login`
.venv/bin/python -m pytest          # offline, no keys, no spend

# safe — nothing is enabled, so there is nothing to spend on
.venv/bin/python -m runner.cli run --modality video --scenarios scenarios/bank-video
```

**Real run** (only after a human enables at least one model in
`configs/models.yaml` — see [video/README.md](video/README.md)):

```bash
.venv/bin/python -m runner.cli run    --modality video --scenarios scenarios/bank-video --budget 85.00
.venv/bin/python -m runner.cli judge  --run <run-id>
.venv/bin/python -m runner.cli report --run <run-id> --open
```

### voice

**Dry run** (free):

```bash
cd voice
python3.12 -m venv .venv                     # or, faster: uv venv --python 3.12 .venv
.venv/bin/pip install -e '.[google,dev]'     # or '.[openai,dev]' — see note below
cp .env.example .env                # ELEVENLABS_API_KEY, GOOGLE_APPLICATION_CREDENTIALS, GCP_PROJECT_ID
.venv/bin/python -m pytest -q       # offline, no keys, no spend
```

Voice splits provider SDKs into extras (`[google]`, `[openai]`) instead of
bundling everything the way the other three's single `.[dev]` does — it
already carries a heavy local speech-recognition model (`faster-whisper`)
as a core dependency, so the split just lets you skip installing a cloud
SDK you won't use. Want both providers? `.[google,openai,dev]`.

**Real run** (spends money once the pre-flight budget check clears — two
models ship enabled):

```bash
.venv/bin/python -m runner.cli run    --budget 2.00 --yes    # --modality defaults to voice
.venv/bin/python -m runner.cli judge
.venv/bin/python -m runner.cli report --open
.venv/bin/python -m runner.cli dashboard --open               # cross-run board, every run at once
# or all four steps in one command:
.venv/bin/python -m runner.cli all --yes --budget 2.00 --judge-budget 1.00
```

Read [voice/HANDOFF.md](voice/HANDOFF.md) §2 before a paid run — it documents
a Google-credential quirk (a short-lived access token workaround) and two
parallelism traps that have cost real quota.

### panel — after you have runs to compare

Never spends, regardless of dry vs real — it only reads other lanes' `runs/`
folders and serves a local page.

```bash
cd panel
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest          # 39 tests, no keys, no network

# 1. export: copy run media to opaque, blinded ids
.venv/bin/python -m runner.cli export \
    --run image=../image/runs/<run-id> \
    --run video=../video/runs/<run-id> \
    --run voice=../voice/runs/<run-id>

# 2. serve: the review page + the vote endpoint
.venv/bin/python -m runner.cli serve --port 8765

# 3. correlate: human majority vs judge, per lane and overall
.venv/bin/python -m runner.cli correlate --out correlation.md --json correlation.json
```

No `.env` needed — `panel/` never calls a model.

### What a run produces

```
<lane>/runs/<run-id>/
├── manifest.json      run id, git sha, models, rubric hashes, cell states,
│                       budget, event log — the frozen record of the setup
├── scenarios/          frozen copy of every scenario THIS run used
├── inputs/             frozen + hashed source assets (edit/reference tasks)
├── outputs/<modality>/<scenario-id>/<model-id>.<ext>   one file per model
├── telemetry.jsonl     one row per generation attempt (retries visible)
├── checks.jsonl        one row per deterministic check result
├── judge.jsonl         one row per judge call: blind map, prompt hash, raw response
├── scores.jsonl        one row per scenario × model: criteria, weights, total
└── report.html         the deliverable — opens anywhere, no server
```

`**/runs/` is **not entirely gitignored**. The heavy, regenerable parts are
excluded — `inputs/`, `outputs/` and every media file
(`.png .jpg .jpeg .webp .mp4 .wav`) — but the **evidence and reports are
committed**: `manifest.json`, every `*.jsonl`, `INDEX.csv` and every rendered
`report.html`. A result has to be defensible without the media sitting next
to it, and "the report says 8.4 — prove it" has to be a five-second answer
from a fresh clone. JSONL rows are append-only; a correction is always a new
run, never an edit to an old one.

## Troubleshooting

The most common way to get stuck here is a Google credential — worth
reading before your first paid run on any Vertex-backed lane, not just
after something breaks.

- **A fresh `gcloud auth application-default login` can still fail** to
  reach a project you have real access to — ADC and the `gcloud` CLI's own
  credential are different OAuth clients, and one can lose authority while
  the other still works. If Vertex calls are refused even though you're
  sure you have access, work around it by minting a short-lived token in
  the same command that starts the run:
  ```bash
  GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)" \
  .venv/bin/python -m runner.cli run ...
  ```
  It expires in about an hour, so generate it fresh each time rather than
  exporting it once. Full detail: [voice/HANDOFF.md](voice/HANDOFF.md) §2.
- **Voice's `--run` only resumes a single scenario.** Pointing `--scenarios`
  at a directory fans out into one run per scenario; passing `--run` in
  that case is refused loudly rather than silently ignored (it used to be
  silently dropped, which re-generated clips that already existed on disk).
- **zsh does not word-split unquoted variables.** Building a flag string
  into a shell variable (`extra="--run $id"`) and expanding it (`$extra`)
  arrives as one argument, not two — pass CLI flags literally instead.

## The cell lifecycle

The unit of work is the **cell**: one scenario × one model (× one task).

```
planned → prepared → generated → checked → measured → judged → scored
   │                     │           │                   │
   └→ skipped            └→ failed   └→ invalid          └→ unjudged
      (task unsupported)    (error/     (gate failed:       (judge failed:
       = n/a, excluded       refused)    the one earned 0)   excluded, never 0)
```

Four terminal states, each reported as itself. **Only `invalid` becomes a
0.** `unjudged` is a dash, excluded from every mean. `skipped` is n/a,
excluded from every denominator.

## Honesty rules

Enforced in code, in every lane:

- A missing key rejects the run **before** any spend — never a failure on
  call 14.
- A pre-flight cost estimate is printed; `--budget` refuses up front and
  aborts mid-run, with the partial run clearly labelled.
- One attempt = one telemetry row; a refusal is never silently retried into
  a clean-looking number.
- Costs are stored as integer micro-USD (millionths of a dollar, so the
  numbers stay exact instead of rounding), derived from returned usage where
  the provider reports it; anything estimated is labelled `estimated` in
  both storage and the report.
- Rubric criteria and weights live in config, never in code, and weights
  must sum to exactly 1.0.
- A blind judge never sees a model or provider name — checked mechanically,
  and the panel's own test (`panel/tests/test_export.py::test_nothing_public_names_a_model`)
  walks every exported byte to confirm it.

## Further reading

- [`docs/`](docs) — dated planning notes and review write-ups
  (scenario-complexity tiers, the 4 Sept Google review, task splits, the
  gaming-scenario plan). Background, not required to run anything.
- [`voice/HANDOFF.md`](voice/HANDOFF.md) — the fullest single write-up in the
  repo: what's measured vs assumed, every instrument bug found and fixed,
  and the open items list. Worth reading even if you're not touching voice,
  as a model for how the other lanes think about noise and false findings.
- Each lane's own `README.md` is the authority on that lane's rubric flow,
  scenario format and CLI — this file is the map, not a substitute for it.
