# Voice lane — handoff

**Last updated 4 September 2026.** Vaibhav is on leave for a week from this
date. Written so this work can be picked up by someone with **no prior
context**, from nothing but this file.

It is not a transcript. It is the state, the decisions, the findings, and the
things we got wrong.

**Contents** — 0 what this is · 1 where things are · 2 running it ·
**3 add a scenario** · **4 where evidence goes** · **5 how dashboards build** ·
6 the bank · 7 findings · 8 bugs fixed · 9 known limits · 10 blocked scenarios ·
11 money · 12 rules · 13 current numbers · 14 the lesson ·
**15 OPEN ITEMS — starts with this week's directed next steps** ·
16 debugging checklist

**If you read only one thing, read §0.** If you are here to run something,
go straight to §2, §3 and §5. If you are here to change something, read §12 first — the
rules there exist because breaking them has already published wrong numbers.

---



## 0 · What this project is, in sixty seconds

We compare **text-to-speech models** on **real industry scenarios** and
publish an evidence-backed answer about where each one is genuinely useful.

- **Two models under test:** `gemini-3-1-flash-tts` (Google) and
`elevenlabs-multilingual-v2` (ElevenLabs).
- **A scenario** is one script plus the checks it must pass — "read this KYC
code so a customer writes it down correctly", "read this compliance
disclaimer at broadcast pace".
- **A run** takes one scenario, generates a clip from every model,
transcribes it, applies hard pass/fail gates, then has an LLM judge score
what survived. Everything it did is written to disk as evidence.
- **The output** is two dashboards: an internal one for us and a client one
we send outside.

**The single most important idea in this project:** a difference between two
models means nothing until it is bigger than the difference a *single* model
shows against *itself* when asked the same thing twice. That second number is
the **noise floor**, and this bank has produced gaps that reversed on a
re-run. Three times, a headline finding turned out to be the measuring
instrument rather than the model. See §11.

---



## 1 · Where everything is


|                  |                                                                                                                             |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **Repo**         | `/Users/laptopbazaar/genmedia-industrial-studies` (GitHub: `tl-ai-labs/genmedia-industrial-studies`)                        |
| **Branch**       | `feat/voice-lane`, cut from `main` at `6ec7f40`                                                                             |
| **Module**       | `voice/` — self-contained beside the existing `image/` module                                                               |
| **Venv**         | `voice/.venv` (Python 3.12, created with `uv`)                                                                              |
| **Credentials**  | `voice/.env` — gitignored, holds `ELEVENLABS_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `GCP_PROJECT_ID`, `OPENAI_API_KEY`. ElevenLabs is called **directly**, not through Vertex — see the note in §15. |
| **Tests**        | `574 passed, 144 skipped` — all offline, no key, no network, no Docker                                                      |
| **Scenarios**    | 17 runnable, 11 blocked (see §10)                                                                                           |
| **Runs on disk** | 36, in `voice/runs/` — gitignored, ~214 MB, **not backed up anywhere**                                                      |


**Commits on the branch** (oldest first):

```
f033f62  Voice lane: TTS model comparison as a self-contained voice/ module
d81f424  Real-use-case scenario bank: 8 scenarios, and the checks they needed
9153a8f  One resolver for the Google credential every Vertex client presents
2c0b5aa  Dashboard: a repeat is the same script, and quality is what was scored
2e4991a  Voice dashboard rebuilt in the image lane's design language
8ffce31  The ASR shared a vendor with an arm, and it changed the answer
d92ee66  Six blocked scenarios unblocked, and four gates that were measuring us
00c4f05  Stream both arms so time-to-first-audio is measured, not inferred
aa547ab  Record why the two code-switching scenarios cannot be measured yet
ed9ef35  Fix three silent dashboard faults: dead players, blank columns, count
499e63f  Set the winner decision band to a flat 0.05, in one place, everywhere
```

`499e63f` is pushed. `origin/main` **has NOT been updated** — a fast-forward
was attempted and refused by tooling; run it when you are ready:

```bash
git push origin feat/voice-lane:main   # clean fast-forward, main is an ancestor
```

**Uncommitted at time of writing** — the whole client report
(`runner/client_report.py`, `runner/audio.py`, two templates, its tests) plus
the latency-median fix and the split-verdict fix in `runner/dashboard.py`.
All of it is green; it needs a commit, not more work.

---



## 2 · Running it

Every command below starts from the module directory:

```bash
cd /Users/laptopbazaar/genmedia-industrial-studies/voice
```



### 2.1 · The free things — do these first

Nothing here spends money, needs a key, or touches the network.

```bash
.venv/bin/python -m pytest -q                              # 574 pass, ~10s
.venv/bin/python -m runner.cli --modality voice dashboard  # internal board
.venv/bin/python -m runner.cli --modality voice client-report
open runs/index.html                    # internal board
open runs/client-report/index.html      # client report
```

**Start here on day one.** If the tests pass and both boards render, your
environment is working and you have not spent a rupee.

### 2.2 · A paid run, end to end

```bash
GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)" \
.venv/bin/python -m runner.cli --modality voice \
  --scenarios scenarios/real-use-cases/vr-ads-06-compliance-disclaimer.yaml \
  all --yes --budget 0.40 --judge-budget 0.20
```

`all` = **run** (generate + transcribe + gate) → **judge** → **report** →
**dashboard**. `--budget` is a hard cap; the run stops rather than exceeding it.
Mint the token **in the same command** — it expires in about an hour.

### 2.3 · The stages, and why you would run them separately


| Command         | Spends              | Does                                                |
| --------------- | ------------------- | --------------------------------------------------- |
| `run`           | **yes** — TTS API   | generates clips, transcribes locally, applies gates |
| `judge`         | **yes** — judge API | scores the clips that cleared their gates           |
| `report`        | no                  | per-run `report.html`                               |
| `dashboard`     | no                  | cross-run internal board                            |
| `client-report` | no                  | the shareable report                                |
| `all`           | yes                 | all of the above in order                           |


Generation and judging are separate because **judging costs money and gates
do not**. If a scenario is failing its gates, re-run `run` until the gates are
right, then judge once. Judging clips that were going to fail anyway is the
easiest way to waste budget here.

### 2.4 · Parallelism — two different things, do not confuse them

**Within one run —** `--workers` **(default 4).** The unit of completion is the
*scenario*: its models are generated concurrently, and the run does not move
on until every model has answered that scenario. That is deliberate — a run
that dies halfway must never leave one model with more scenarios than the
other, because the two means would then be over different work.

```bash
.venv/bin/python -m runner.cli --modality voice \
  --scenarios scenarios/real-use-cases/ run --workers 6 --yes --budget 3.00
```

**Across runs — one shell per pass.** Pointing `--scenarios` at a *directory*
mints **one run per scenario** by default. To get a second independent pass
for the noise floor, run the whole bank again with a different `--label`:

```bash
# pass 1 and pass 2, in two terminals, at the same time
… --scenarios scenarios/real-use-cases/ all --label p1 --yes --budget 6.00
… --scenarios scenarios/real-use-cases/ all --label p2 --yes --budget 6.00
```

Runs are fully independent — separate folders, separate telemetry, nothing
shared — so parallel passes are safe. **Two passes minimum for anything you
intend to quote.** One pass has no noise floor and its gap is not evidence.

Two more flags worth knowing:

- `--bundle` puts every scenario in **one** run folder instead of one each.
- `--repeat N` issues the same scenario N times *inside* one run. This is a
**throughput batch**, not a noise floor — it measures cost and speed at
volume, and it is what `vr-ecom-07` and `vr-game-07` use.



### 2.5 · Three quirks that will bite you

1. **Google credentials are on a borrowed access token.** ADC on this machine
  lost all authority in the org mid-session — a fresh
   `gcloud auth application-default login` produced a credential that could
   not call `resourcemanager.projects.get` on a project the same human is
   Editor of, and could see **zero projects**, while the gcloud CLI
   credential worked fine. They are different OAuth clients
   (`764086051850-…` vs `32555940559…`). Workaround: pass
   `GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"`, which
   `runner/gcp_auth.py` turns into explicit credentials. **It expires in ~1
   hour**, so mint it in the same command that starts the run. Proper fix:
   have an Owner grant `roles/aiplatform.user` to
   `harness-vertex@ai-studies-console.iam.gserviceaccount.com` (it exists,
   named for this harness, and currently holds **no roles**) and issue a key.
2. `--run` **resume only works for a single scenario.** Pointing
  `--scenarios` at a directory fans out and mints one run per scenario; the
   flag is now *refused loudly* in that case rather than silently dropped
   (it used to be dropped, which cost 741 characters of metered quota
   re-generating clips already on disk).
3. **zsh does not word-split unquoted variables.** `extra="--run $id"` then
  `$extra` arrives as ONE argument. Pass flags literally.

---



## 3 · How to add a scenario

A scenario is **one YAML file** in `scenarios/real-use-cases/`. It is picked
up automatically — there is no registry to edit.

```yaml
id: vr-ecom-09                    # unique; the industry prefix drives grouping
modality: voice
task: text_to_speech              # or styled_tts if you set input.style
title: "Order status readback - Ecommerce & Retail"

input:
  script: |                       # THE EXACT WORDS BOTH MODELS SPEAK
    Your order 4 4 1 9 ships today from our Pune warehouse.
  language: en-IN
  style: "calm, unhurried support agent"   # styled_tts only

params:
  voice: female_mid_warm          # a LOGICAL voice, mapped per model in
  format: wav                     # configs/models.yaml - never a provider id
  sample_rate: 24000
  speed: 1.0

expected: |                       # plain English, for the judge and for humans
  1) The order number intelligible digit by digit
  2) Unhurried pace - a caller is writing this down

checks:                           # HARD GATES. Any failure invalidates the clip.
  duration_s: {min: 4.0, max: 20.0}
  max_silence_s: 2.0
  no_clipping: true
  max_wer: 0.08
  must_say_digits: "4419"
  must_say: ["Pune"]

tags: [ecommerce, readback, vr-ecom-09]
```

**The rules, learned the hard way:**

1. **Every gate must be passable by a perfect reading.** `tests/test_scenario_bank.py`
  enforces this. A gate that no correct performance can pass measures us, not
   the model — we shipped four of those and they produced false failures.
2. **Write phrases the way the transcriber writes them.** The ASR outputs
  numerals, so `must_say: ["1814"]` works and `must_say: ["eighteen fourteen"]`
   does not. Use `must_say_digits` for numbers; it normalises both sides.
3. **Prefer a check that can fail Google.** The point is to find where Google
  is genuinely strong, which is worthless if the gate cannot go the other way.
4. **A scenario that cannot be measured goes in** `blocked/scenarios/`, not
  `scenarios/`. Anything in `scenarios/` runs and generates paid clips; a
   scenario that produces audio but cannot check its own claim manufactures a
   number that looks like evidence. `blocked/` is still validated by the tests.

Then:

```bash
.venv/bin/python -m pytest tests/test_scenario_bank.py -q   # free, catches most mistakes
… --scenarios scenarios/real-use-cases/vr-ecom-09-*.yaml all --yes --budget 0.40
```

**Before trusting a new gate, prove it discriminates**: it must pass a correct
reading and fail a wrong one. Promoting `vr-ecom-06` without doing this
produced two false Gemini failures from a hyphen.

---



## 4 · Where the evidence goes

One run = one folder under `voice/runs/<date>_<time>_voice-<label>/`:

```
manifest.json     what was run: models, scenarios + content hashes, judge,
                  ASR, git sha, pricing. The frozen record of the setup.
telemetry.jsonl   ONE LINE PER MODEL CALL - timings, cost, provider ids,
                  the voice actually used, the output path, TTFA if streamed
checks.jsonl      one line per clip: every gate, pass/fail, measurements,
                  the raw transcript the gates were applied to
judge.jsonl       one line per judged clip: criterion scores + reasoning,
                  and the blind label it was judged under
scores.jsonl      the weighted score per clip, and its status
                  (scored | invalid | unjudged)
group_checks.jsonl  set-level verdicts that live BETWEEN clips
                  (voice consistency, speaker distinctness)
summary.json      derived arithmetic. Not evidence - regenerate freely.
scenarios/        a COPY of every scenario yaml as it was that day
inputs/           the frozen inputs
outputs/voice/<scenario>/<model>.wav   the clips, plus .txt transcripts
report.html       the per-run report
```

**Three rules about this folder:**

- **It is append-only evidence.** Never edit a `.jsonl` by hand. A correction
is a new run, not a rewritten record.
- `runs/` **is gitignored** (`**/runs/`*) and lives **only on this machine**.
214 MB, no backup. If the laptop dies, every clip and every measurement is
gone. Nothing else in this handoff matters more than that sentence.
- **The scenario copy is why re-runs are comparable.** The dashboard reads
each run's *own frozen* scenario, so editing a scenario today does not
silently redefine what an old run measured. Runs of an edited scenario are
excluded from spreads and counted separately.

---



## 5 · How the dashboards are generated

Both are **pure functions of what is already on disk.** No API calls, no
spend, no network. Delete them and regenerate; nothing is lost.

```
runs/*/  ──►  load_runs()  ──►  rollup_models()  ──►  _scenario_blocks()
                                        │
                        ┌───────────────┴────────────────┐
                        ▼                                ▼
              runner/dashboard.py               runner/client_report.py
              runs/index.html                   runs/client-report/index.html
              (internal: everything)            (client: simplified)
```

**The client report imports its numbers from** `dashboard.py`**.** It computes no
arithmetic of its own. That is the rule that keeps the two from disagreeing —
and it was learned by watching a report deciding at 0.5 and a board deciding
on measured noise contradict each other about the same runs.

```bash
.venv/bin/python -m runner.cli --modality voice dashboard        # internal
.venv/bin/python -m runner.cli --modality voice client-report    # client, folder
.venv/bin/python -m runner.cli --modality voice client-report --inline
```

The client report has two shapes. **Default is a folder** —
`runs/client-report/` with a 184 KB page and the clips in `audio/` beside it;
share the whole folder. `--inline` produces one self-contained 22 MB
`.html` with every clip embedded — the right shape when it has to travel as a
single attachment. `--audio-quality 0.8` trades quality for size (~14 MB).

---



## 6 · What this bank is

28 scenarios from the **"Voice — real use cases"** tab of
`~/Downloads/updated_scenarios.xlsx`. Every one is anchored to a named
deployment with a source, and each carries the narrow sales claim it would
support if it passes.

- **17 runnable** → `voice/scenarios/real-use-cases/`
- **11 blocked** → `voice/blocked/scenarios/` (deliberately *outside* the
default run path, so they cannot silently generate cells that look scored;
still validated by `tests/test_scenario_bank.py`, so each is correct the
day its blocker lifts). See `voice/blocked/README.md`.

**Two models under test:** `elevenlabs-multilingual-v2` and
`gemini-3-1-flash-tts`. The goal, stated by the user, is to find **where
Google models are genuinely useful** — not to show Google is best. Claims are
written narrowly and every decisive gate can fail Google.

---



## 7 · Findings, by how much I'd trust them



### Trustworthy — no transcriber in the path, stable across every instrument change

- `vr-ads-06` **(compliance disclaimer) — the strongest Google result.**
Gemini passed every gate in every run: **~200 wpm at zero word errors**.
ElevenLabs failed the ≥190 wpm rate gate every time (~131 wpm). Gemini can
deliver broadcast-pace legal copy; ElevenLabs cannot.
- **Timing: Gemini is consistently closer.** `vr-ads-02` (15.0s ±0.1):
Gemini +0.48s vs ElevenLabs +2.54s. `vr-drama-04` (11.0s ±0.15): Gemini
+0.66s vs ElevenLabs −1.45s. **Both models fail both gates**, but Gemini
misses *long both times* — correctable by writing ~4% shorter copy —
whereas ElevenLabs missed long on one and short on the other, which no copy
adjustment fixes.
- `vr-drama-02` **(ACX audiobook spec): both models fail.** Neither ships to
Audible without a limiting pass. Both exceed the −3 dBFS peak ceiling; which
of RMS/peak each misses varies slightly by run. **This is the loss to pair
with the timing wins** — a seller who volunteers one weakness gets believed
on the rest.



### Do not quote yet — moved three times as the instrument changed

Everything ASR-mediated: WER, `must_say`, `must_say_digits`. Three ASR
configurations (Gemini, Whisper `small.en`, Whisper `medium`) produced three
different verdicts on the same audio. The normalizer notation bugs behind much
of that are now fixed, but these need **both passes to agree** before use.

### Retracted — claims I made that did not survive

- ~~"ElevenLabs wins the announcer scenario by 1.37"~~ — Gemini's own
run-to-run spread on that scenario is **±1.193**. The gap is inside the
noise. This is the single best illustration of why repeats matter.
- ~~"Both models read the 12-digit reference exactly right"~~ — that was one
run. On the second, Gemini inserted an extra digit.
- ~~"Gemini pronounced Bhiwandi correctly where ElevenLabs didn't"~~ — did not
survive a second run, and the gate has since been retired as unmeasurable.
- ~~"Gemini 9.67 vs ElevenLabs 0.00 on the mission briefing"~~ — an artifact.
The ASR wrote "cash Delta" for "Cache Delta", homophones. Corrected, the
scenario is a **dead heat** (9.526 vs 9.478).

---



## 8 · The instrument bugs found and fixed

Every one of these failed **correct** readings, usually on **both** models
identically — the signature of an instrument fault, not a model fault.


| Bug                                   | Effect                                                                                                                                                                                                                                                      |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **ASR shared a vendor with an arm**   | `gemini-2.5-flash` transcribing while `gemini-3.1-flash-tts` was under test. Measured over 40 clips: median WER gap **+0.0103 → +0.0001** when a neutral recogniser listened. ElevenLabs got *better*, Gemini slightly *worse*. Now local Whisper `medium`. |
| Indian digit grouping                 | `2,50,000` read as "two fifty thousand"; `1,00,000` as "one zero"                                                                                                                                                                                           |
| `%` stripped as punctuation           | "thirty percent" vs "30%" shared nothing                                                                                                                                                                                                                    |
| Digit ordinals                        | "14th" vs "fourteenth"                                                                                                                                                                                                                                      |
| `harbour`/`harbor`                    | Missing from a list that already folded `colour`, `favour`                                                                                                                                                                                                  |
| Clock times                           | "between two and four" vs "2:00 and 4:00" inserted 4 tokens                                                                                                                                                                                                 |
| **Space/hyphen-grouped identifiers**  | `481 902 773 154` yielded the digits **192731** — a perfect readback failing its own gate                                                                                                                                                                   |
| **Currency symbol overrode grouping** | Whisper wrote `$2,50,000` for rupees → "two hundred and fifty thousand **dollars**", firing the very `must_not_say` gate meant to catch that reading                                                                                                        |
| `must_say` was substring              | `"lakh"` was satisfied by `"Lakhsmi"`                                                                                                                                                                                                                       |
| Proper-noun gates                     | Three ASRs wrote *Vazquez*, *Bawande*, *Bhavandi*, *Ragavan* for correct readings. **Retired — pronunciation is a judge criterion; the judge hears the clip.**                                                                                              |
| Dashboard: gated cells differenced    | `vr-ads-06` read "+9.650, larger than noise" — one model's score minus the other's *failure*                                                                                                                                                                |
| Dashboard: runs sharing a label       | Collapsed into one column; the same score rendered twice, looking like perfect reproducibility                                                                                                                                                              |
| Dashboard: quality averaged zeros     | Board read **2.4/10** for two models tied at ~9.8 on the one scenario they both cleared                                                                                                                                                                     |
| `--run` silently dropped              | Cost 741 characters of metered quota                                                                                                                                                                                                                        |
| Scenario `criteria`/`weights`         | Validated (rejecting anything not summing to 1.0) then never read — validation theatre                                                                                                                                                                      |


---



## 9 · Known limits, unfixed

1. **The judge shares a vendor with one arm.** `gemini-2.5-flash`, listening
  to audio, while one arm is Gemini. It drives **62–68%** of the weighted
   score. Blinding (A/B/C, shuffled on `sha256(scenario_id)`) hides the label,
   not the acoustic fingerprint. **The user has accepted this deliberately**
   — do not re-litigate it; it is disclosed in a dashboard footnote generated
   from each run's own manifest. `OpenAIAudioJudge` is already implemented and
   registered in `runner/judge.py`; switching is a four-line config change,
   blocked only on OpenAI credit (the account returns `429  insufficient_quota`). Given the ASR bias reversed a finding, the same
   cross-check on the judge is the highest-value experiment left, at roughly
   **$0.10 for all clips** — no generation, no ElevenLabs quota.
2. **Calibration gate never run.** 2 humans × 5 clips. Until then
  `naturalness` and `clarity` carry `calibration_trusted: false` and are
   badged `unc`.
3. `audio_quality` **is a signal metric, not a MOS** — 15% of the score at a
  50/50 blend. Labelled everywhere. UTMOSv2 is the intended replacement.
   (Note: DNSMOS is the *wrong* choice for TTS — it is denoise-tuned.)
4. **The judge is pointwise, not pairwise/CMOS** — the weaker protocol.
5. `vr-ecom-03`**'s numbering claim cannot be gated by any Whisper**, which
  normalises spoken numbers into digits and so destroys whether the model
   said "lakh" or "hundred thousand". The finding *both models say lakh* rests
   on a one-off **forced word-form transcription** (an instructable LLM ASR
   told to write numbers as words) — the only reliable method, and it needs a
   transcriber you can prompt. Whisper cannot be told.

---



## 10 · What blocks the remaining 11 scenarios

Full detail in `voice/blocked/README.md`. Ranked by leverage:


| Capability                       | Unlocks | Notes                                                                                                                                                                         |
| -------------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Speaker-embedding cosine**     | 5       | **Verified feasible** — `resemblyzer` installed and discriminating on our own clips (0.969 self vs 0.741 cross). Needs `setuptools<81` for `pkg_resources`. Highest leverage. |
| Batch throughput reporting       | 3       | No new instrument — cost/latency/failures already recorded per call                                                                                                           |
| Blind human listener panel       | 3       | Not a code problem                                                                                                                                                            |
| Multilingual ASR + native review | 2       | Per-*segment* WER, not whole-clip                                                                                                                                             |
| Streaming / time-to-first-audio  | 2       | Includes `VR-GAME-03` — Fortnite's AI Darth Vader ran on Gemini 2.0 Flash, the largest shipped example of real-time NPCs. Strongest Google story in the workbook.             |
| Character-exact alphanumeric     | 1       | Smallest build — the digit path over letters                                                                                                                                  |
| Far-field simulation             | 1       | numpy/scipy                                                                                                                                                                   |
| Multi-speaker + diarisation      | 1       | Largest build                                                                                                                                                                 |
| Locally-hosted arms              | 1       | Not a harness feature                                                                                                                                                         |


---



## 11 · Money and quota

- **ElevenLabs: Creator plan, ~112,352 of 121,298 characters left.** One full
pass of the 10 runnable scenarios ≈ **3,428 characters**. Roughly 32 passes
remain this cycle. Check with the `/v1/user/subscription` endpoint — **it
lags**, and a stale read misled me once; re-query if a number looks wrong.
- **Total API spend for the day: well under $2.** Cost is not the constraint;
the instrument is.

---



## 12 · Standing rules for whoever picks this up

- **Never fabricate outputs, transcripts, scores or costs.** Never count
stubbed output as working generation.
- **Pause for explicit approval before** `git commit`**,** `git push`**, or deploy.**
Local verification is always fine.
- Omit the `Co-Authored-By` trailer from commit messages.
- PRs target `develop` in the *other* repo; this repo's convention is
feature branch → `main` via PR, and `main` is the manager's.
- `../ai-studies-console` and its `genmedia-eval/` are read-only history. The
voice lane lives here now; four old runs there were never migrated.

---



## 13 · Where the numbers stand today

Read these off the board rather than trusting this table in a week — but this
is the shape of it as of 4 September.


|                       | Gemini      | ElevenLabs |
| --------------------- | ----------- | ---------- |
| Quality (rubric, %)   | **95.1**    | 93.4       |
| Gates passed          | 88.2%       | **89.8%**  |
| Run-to-run spread     | 22.3pp      | **9.0pp**  |
| Worst word error rate | 47.9%       | **13.6%**  |
| Cost per clip         | **$0.0069** | $0.0144    |
| Cost per audio minute | **$0.0430** | $0.0991    |
| Latency, median       | 7.65s       | **1.67s**  |
| Latency, 95th         | 31.97s      | **4.32s**  |


**Overall verdict: Split.** Gemini leads on quality and on both cost measures;
ElevenLabs leads on gates, consistency, worst-case accuracy and both latency
measures. Naming either the winner hides half the evidence.

**Per scenario: 5 decided, and 4 of those 5 have a gap smaller than the noise
floor measured on that same scenario.** They are flagged in red on both
boards. Treat them as provisional; they can invert on a re-run.

The one result that is solid: `vr-ecom-06`**, KYC confusable readback** —
Gemini ahead by 10.3pp against a ±0.3pp floor, roughly 33× the noise. That is
the single quotable Google win in the bank.

---



## 14 · The lesson worth carrying

Three separate times today a headline finding turned out to be the
**instrument**, not the model: the announcer gap was noise, the Google word-
accuracy lead was the Google recogniser, and a 9.67-vs-0.00 landslide was a
homophone. In each case the tell was the same — **a failure that hit both
models identically, or a gap that moved when nothing about the models had.**

Two habits caught all three, and both are cheap:

- **Run it twice.** A single measurement has no noise floor, and this bank
has produced gaps that reversed on the second pass.
- **Ask what else could produce this number.** If the measuring instrument
shares a vendor, a language, or a spelling convention with one side, it is
a suspect until cleared.

---



## 15 · OPEN — everything unfinished, most urgent first



### Directed next steps — voice lane (from the manager, 4 September)

These are the priorities for the coming week. Each carries what already
exists, so nobody rebuilds something that is already here.

| # | Task | What is already built, and what is actually missing |
|---|---|---|
| A | **Retest TTS latency in streaming mode.** Gemini's median whole-call latency is **7.65s** against ElevenLabs' 1.67s, and that number is unflattering partly because it measures the wrong thing for a conversational use case. | **Streaming is already built** — both adapters stream and timestamp the first audio chunk (`measure_ttfa` on `GenRequest`, commit `00c4f05`). What is missing is *coverage*: only **one** scenario (`vr-game-03`) sets `max_ttfa_ms`, so only one scenario is measured that way. Add `max_ttfa_ms` to the scenarios where responsiveness is the claim and re-run. On the one scenario we do have, TTFA is Gemini 2084 ms vs ElevenLabs 1292 ms (p50, n=20) — closer than 7.65s vs 1.67s, but still behind. **Do not report whole-call latency as TTFA; they differ by roughly an order of magnitude.** |
| B | **Compare against ElevenLabs v3, not v2.** The whole bank so far is `eleven_multilingual_v2`. | **v3 is not configured** — `configs/models.yaml` has `eleven_multilingual_v2` (enabled) and `eleven_flash_v2_5` (disabled), and no v3 entry. Add it, then **re-run both arms from scratch**: v2 and v3 results are not comparable, and every published number so far is against v2. Note this resets the noise floor too. |
| C | **Restructure the bank into segments:** call centre (retail / telco / banking) and micro-drama. | Today's split is by id prefix — `ecom` 6, `game` 5, `ads` 3, `drama` 3 — and `INDUSTRY` in `runner/dashboard.py` maps those prefixes to labels. Segments are a **re-grouping, not new scenarios**: several `ecom` scenarios (KYC readback, order status) are already call-centre work. Telco and banking have no scenarios yet. Changing an id changes its hash, so **re-tag rather than rename** if you want existing runs to stay comparable. |

**Where the manager expects the story to land**, worth testing rather than assuming: *language and emotion are the strengths; voice drift is the concern.* Two of those three are already measured —

- **Voice drift is real and quantified.** Speaker-embedding self-similarity across sessions: ElevenLabs **0.976**, Gemini **0.859** (1.0 = identical). Gemini also failed the six-NPC distinctness gate that ElevenLabs passed (`vr-game-04`). The instrument for this exists: `runner/voiceprint.py`.
- **Emotion is not measured at all.** The three scenarios that would test it need a blind human listener panel (§10) — an LLM judge recognising an emotion is a weaker claim than an audience recognising it.
- **Language is partly blocked.** Hindi–English code-switching is evidenced *unmeasurable* with the current recogniser (commit `aa547ab`); single-language non-English is measurable and untested.

---

### Note — ElevenLabs may be reachable through Vertex

Recorded 4 September, **not yet verified by us.**

We currently call ElevenLabs **directly**, with `ELEVENLABS_API_KEY` from
`voice/.env`, through `runner/adapters/elevenlabs_tts.py` (plain
`urllib.request` against `api.elevenlabs.io`). ElevenLabs models are reported
to be available through **Google Vertex AI** as well. If the models this study
needs — specifically **v3**, per task B — are offered there, we may have to
route through Vertex instead of the direct API.

Before anyone starts that work, check three things:

1. **Is the exact model there?** Vertex Model Garden availability lags, and
   the version matters — a v3 comparison run against a Vertex build that is
   really v2.5 would be a silent, unrecoverable error in the results.
2. **Does the billing change?** Vertex bills to the GCP project, not the
   ElevenLabs plan. That moves cost off the character quota tracked in §11
   and onto a different wallet, and **every cost number on the boards would
   become incomparable with the ones beside it** unless re-run.
3. **Does it change what we are measuring?** Routing both arms through Google
   infrastructure means the transport is no longer independent of one vendor.
   That is the same class of exposure as the judge and the retired Google ASR,
   and it must be disclosed on the client report if it happens.

The adapter is small and the swap is not hard. The evidence consequences are
the expensive part — treat a gateway change like a model change: **new runs,
not a re-label of old ones.**

---

### Needs doing before anything else


| #   | Item                                                                                                                                                                                                               | Why it matters                                                                                                                              |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Commit the working tree.** `runner/client_report.py`, `runner/audio.py`, both client templates, `tests/test_client_report.py`, plus the latency and split-verdict fixes in `dashboard.py`. All green, 574 tests. | It is a full feature sitting untracked on one laptop.                                                                                       |
| 2   | **Back up** `voice/runs/`**.** 214 MB, gitignored, on one machine, no copy anywhere.                                                                                                                               | Every clip and every measurement in this study is there. Losing it means re-spending the whole budget to re-derive numbers we already have. |
| 3   | `git push origin feat/voice-lane:main` — a clean fast-forward that was attempted and refused by tooling.                                                                                                           | `main` still has only the image lane; the voice work is invisible to anyone cloning it.                                                     |




### Decisions waiting on a human


| #   | Item                                                                    | Detail                                                                                                                                                                                                                                                                                                                       |
| --- | ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 4   | **The 0.05 band is doing real damage to result quality.**               | Set on the Ravi's instruction, replacing a measured noise floor. It moved three published verdicts in one day and 4 of 5 scenario winners now sit inside their own noise. The page discloses this everywhere, but the underlying issue is a threshold chosen before the data rather than from it. Worth revisiting with him. |
| 5   | **The judge is a Google model judging a comparison with a Google arm.** | `gemini-2.5-flash`. Disclosed up front on the client report because the Google team will spot it. The fix is a neutral judge cross-check (~$5 of OpenAI credit) — the highest-value remaining experiment, and it was already the top recommendation a day ago.                                                               |
| 6   | **The judge has never been calibrated.**                                | The 2-humans × 5-clips gate has never run. Until it does, `naturalness` and `clarity` carry no evidence of agreeing with a human ear. Needs two people and about an hour.                                                                                                                                                    |




### Work with a known shape


| #   | Item                                                                                           | Detail                                                                                                                                                                                                                                       |
| --- | ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 7   | **Three batch runs were never judged** — `vr-ecom-07`, `vr-game-07`, `vr-game-03` (164 clips). | Their cost, throughput and TTFA figures are valid; they carry no quality score and show "no score". Running `judge` on those three runs would complete them.                                                                                 |
| 8   | **11 scenarios still blocked.**                                                                | 3 need a human listener panel, 2 are evidenced unmeasurable (Hindi–English code-switching — see `aa547ab`), 1 needs locally-hosted models, the rest need diarisation or codec conditions wired to a scenario. Detail in `blocked/README.md`. |
| 9   | `--repeat` **batch clips are unscored by design but render as "No score".**                    | Correct but reads like failure on the board. Worth a distinct state.                                                                                                                                                                         |
| 10  | **Google credentials still depend on a 1-hour token.**                                         | Proper fix: an Owner grants `roles/aiplatform.user` to `harness-vertex@ai-studies-console.iam.gserviceaccount.com` (exists, currently holds no roles) and issues a key.                                                                      |




### Known and deliberately not fixed


| #   | Item                                                                            | Detail                                                                                                                                                                                                                                       |
| --- | ------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 11  | **Per-run** `report.html` **files from before 4 Sept have broken audio links.** | Six of them. The `#`-in-URL bug is fixed in code, but regenerating those reports would render old runs against *today's* scenarios and misdescribe what was spoken. Leave them, or fix `cmd_report` to read the run's own frozen copy first. |
| 12  | **The published Claude artifact** `63dfb62a…` **is private and stale.**         | Delivery moved to the `.html` file. Ignore the artifact unless someone asks.                                                                                                                                                                 |
| 13  | **ElevenLabs quota is unverified.**                                             | Last known ~112k characters on the Creator plan; the subscription endpoint lags and misled us once. Re-query before a big run; one full pass of the 17 scenarios is roughly 6k characters.                                                   |


---



## 16 · If something looks wrong, check these first

In the order that has actually caught problems here:

1. **Did it fail for both models identically?** Then it is the instrument, not
  the models. Every one of the three retracted findings had this signature.
2. **Did the number move when nothing about the models moved?** Same
  conclusion.
3. **Does the measuring tool share a vendor, language or spelling convention
  with one arm?** A Google recogniser measurably favoured the Google arm and
   was retired for it. The judge still has this exposure.
4. **Has the scenario been run twice?** If not, there is no noise floor and
  the gap is not evidence yet.
5. **Is the gate passable by a perfect reading?** Four gates here were not.
6. **Are you comparing runs of the same version of the scenario?** The board
  excludes edited-scenario runs from spreads; check the "excluded" note on
   the card.

