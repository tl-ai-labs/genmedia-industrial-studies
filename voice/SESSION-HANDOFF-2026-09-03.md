# Session handoff — voice lane, 3 September 2026

Written so this work can be resumed from a different account or a fresh
session with nothing but this file. It is not a transcript; it is the state,
the decisions, the findings, and the things I got wrong.

---

## 1 · Where everything is

| | |
|---|---|
| **Repo** | `/Users/laptopbazaar/genmedia-industrial-studies` (GitHub: `tl-ai-labs/genmedia-industrial-studies`) |
| **Branch** | `feat/voice-lane`, cut from `main` at `6ec7f40` |
| **Module** | `voice/` — self-contained beside the existing `image/` module |
| **Venv** | `voice/.venv` (Python 3.12, created with `uv`) |
| **Credentials** | `voice/.env` — gitignored, holds `ELEVENLABS_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `GCP_PROJECT_ID`, `OPENAI_API_KEY` |
| **Tests** | `419 passed, 124 skipped` — all offline, no key, no network |

**Commits on the branch** (oldest first):

```
f033f62  Voice lane: TTS model comparison as a self-contained voice/ module
d81f424  Real-use-case scenario bank: 8 scenarios, and the checks they needed
9153a8f  One resolver for the Google credential every Vertex client presents
2c0b5aa  Dashboard: a repeat is the same script, and quality is what was scored
2e4991a  Voice dashboard rebuilt in the image lane's design language
8ffce31  The ASR shared a vendor with an arm, and it changed the answer
```

**Uncommitted at time of writing** — the normalizer notation fixes, the gate
retirements, `blocked/`, and two new scenarios. Commit these; nothing is
half-finished.

Nothing has been pushed. The user has approved commits but not a push.

---

## 2 · Running it

```bash
cd /Users/laptopbazaar/genmedia-industrial-studies/voice

# One scenario, end to end (generate → check → judge → report → dashboard)
GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)" \
.venv/bin/python -m runner.cli --modality voice \
  --scenarios scenarios/real-use-cases/vr-ads-06-compliance-disclaimer.yaml \
  all --yes --budget 0.40 --judge-budget 0.20

# Cross-run dashboard only (free, no spend)
.venv/bin/python -m runner.cli --modality voice dashboard

# Everything offline
.venv/bin/python -m pytest -q
```

### Three quirks that will bite you

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
2. **`--run` resume only works for a single scenario.** Pointing
   `--scenarios` at a directory fans out and mints one run per scenario; the
   flag is now *refused loudly* in that case rather than silently dropped
   (it used to be dropped, which cost 741 characters of metered quota
   re-generating clips already on disk).
3. **zsh does not word-split unquoted variables.** `extra="--run $id"` then
   `$extra` arrives as ONE argument. Pass flags literally.

---

## 3 · What this bank is

28 scenarios from the **"Voice — real use cases"** tab of
`~/Downloads/updated_scenarios.xlsx`. Every one is anchored to a named
deployment with a source, and each carries the narrow sales claim it would
support if it passes.

- **10 runnable** → `voice/scenarios/real-use-cases/`
- **18 blocked** → `voice/blocked/scenarios/` (deliberately *outside* the
  default run path, so they cannot silently generate cells that look scored;
  still validated by `tests/test_scenario_bank.py`, so each is correct the
  day its blocker lifts). See `voice/blocked/README.md`.

**Two models under test:** `elevenlabs-multilingual-v2` and
`gemini-3-1-flash-tts`. The goal, stated by the user, is to find **where
Google models are genuinely useful** — not to show Google is best. Claims are
written narrowly and every decisive gate can fail Google.

---

## 4 · Findings, by how much I'd trust them

### Trustworthy — no transcriber in the path, stable across every instrument change

- **`vr-ads-06` (compliance disclaimer) — the strongest Google result.**
  Gemini passed every gate in every run: **~200 wpm at zero word errors**.
  ElevenLabs failed the ≥190 wpm rate gate every time (~131 wpm). Gemini can
  deliver broadcast-pace legal copy; ElevenLabs cannot.
- **Timing: Gemini is consistently closer.** `vr-ads-02` (15.0s ±0.1):
  Gemini +0.48s vs ElevenLabs +2.54s. `vr-drama-04` (11.0s ±0.15): Gemini
  +0.66s vs ElevenLabs −1.45s. **Both models fail both gates**, but Gemini
  misses *long both times* — correctable by writing ~4% shorter copy —
  whereas ElevenLabs missed long on one and short on the other, which no copy
  adjustment fixes.
- **`vr-drama-02` (ACX audiobook spec): both models fail.** Neither ships to
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

## 5 · The instrument bugs found and fixed

Every one of these failed **correct** readings, usually on **both** models
identically — the signature of an instrument fault, not a model fault.

| Bug | Effect |
|---|---|
| **ASR shared a vendor with an arm** | `gemini-2.5-flash` transcribing while `gemini-3.1-flash-tts` was under test. Measured over 40 clips: median WER gap **+0.0103 → +0.0001** when a neutral recogniser listened. ElevenLabs got *better*, Gemini slightly *worse*. Now local Whisper `medium`. |
| Indian digit grouping | `2,50,000` read as "two fifty thousand"; `1,00,000` as "one zero" |
| `%` stripped as punctuation | "thirty percent" vs "30%" shared nothing |
| Digit ordinals | "14th" vs "fourteenth" |
| `harbour`/`harbor` | Missing from a list that already folded `colour`, `favour` |
| Clock times | "between two and four" vs "2:00 and 4:00" inserted 4 tokens |
| **Space/hyphen-grouped identifiers** | `481 902 773 154` yielded the digits **192731** — a perfect readback failing its own gate |
| **Currency symbol overrode grouping** | Whisper wrote `$2,50,000` for rupees → "two hundred and fifty thousand **dollars**", firing the very `must_not_say` gate meant to catch that reading |
| `must_say` was substring | `"lakh"` was satisfied by `"Lakhsmi"` |
| Proper-noun gates | Three ASRs wrote *Vazquez*, *Bawande*, *Bhavandi*, *Ragavan* for correct readings. **Retired — pronunciation is a judge criterion; the judge hears the clip.** |
| Dashboard: gated cells differenced | `vr-ads-06` read "+9.650, larger than noise" — one model's score minus the other's *failure* |
| Dashboard: runs sharing a label | Collapsed into one column; the same score rendered twice, looking like perfect reproducibility |
| Dashboard: quality averaged zeros | Board read **2.4/10** for two models tied at ~9.8 on the one scenario they both cleared |
| `--run` silently dropped | Cost 741 characters of metered quota |
| Scenario `criteria`/`weights` | Validated (rejecting anything not summing to 1.0) then never read — validation theatre |

---

## 6 · Known limits, unfixed

1. **The judge shares a vendor with one arm.** `gemini-2.5-flash`, listening
   to audio, while one arm is Gemini. It drives **62–68%** of the weighted
   score. Blinding (A/B/C, shuffled on `sha256(scenario_id)`) hides the label,
   not the acoustic fingerprint. **The user has accepted this deliberately**
   — do not re-litigate it; it is disclosed in a dashboard footnote generated
   from each run's own manifest. `OpenAIAudioJudge` is already implemented and
   registered in `runner/judge.py`; switching is a four-line config change,
   blocked only on OpenAI credit (the account returns `429
   insufficient_quota`). Given the ASR bias reversed a finding, the same
   cross-check on the judge is the highest-value experiment left, at roughly
   **$0.10 for all clips** — no generation, no ElevenLabs quota.
2. **Calibration gate never run.** 2 humans × 5 clips. Until then
   `naturalness` and `clarity` carry `calibration_trusted: false` and are
   badged `unc`.
3. **`audio_quality` is a signal metric, not a MOS** — 15% of the score at a
   50/50 blend. Labelled everywhere. UTMOSv2 is the intended replacement.
   (Note: DNSMOS is the *wrong* choice for TTS — it is denoise-tuned.)
4. **The judge is pointwise, not pairwise/CMOS** — the weaker protocol.
5. **`vr-ecom-03`'s numbering claim cannot be gated by any Whisper**, which
   normalises spoken numbers into digits and so destroys whether the model
   said "lakh" or "hundred thousand". The finding *both models say lakh* rests
   on a one-off **forced word-form transcription** (an instructable LLM ASR
   told to write numbers as words) — the only reliable method, and it needs a
   transcriber you can prompt. Whisper cannot be told.

---

## 7 · What blocks the other 18 scenarios

Full detail in `voice/blocked/README.md`. Ranked by leverage:

| Capability | Unlocks | Notes |
|---|---|---|
| **Speaker-embedding cosine** | 5 | **Verified feasible** — `resemblyzer` installed and discriminating on our own clips (0.969 self vs 0.741 cross). Needs `setuptools<81` for `pkg_resources`. Highest leverage. |
| Batch throughput reporting | 3 | No new instrument — cost/latency/failures already recorded per call |
| Blind human listener panel | 3 | Not a code problem |
| Multilingual ASR + native review | 2 | Per-*segment* WER, not whole-clip |
| Streaming / time-to-first-audio | 2 | Includes **`VR-GAME-03`** — Fortnite's AI Darth Vader ran on Gemini 2.0 Flash, the largest shipped example of real-time NPCs. Strongest Google story in the workbook. |
| Character-exact alphanumeric | 1 | Smallest build — the digit path over letters |
| Far-field simulation | 1 | numpy/scipy |
| Multi-speaker + diarisation | 1 | Largest build |
| Locally-hosted arms | 1 | Not a harness feature |

---

## 8 · Money and quota

- **ElevenLabs: Creator plan, ~112,352 of 121,298 characters left.** One full
  pass of the 10 runnable scenarios ≈ **3,428 characters**. Roughly 32 passes
  remain this cycle. Check with the `/v1/user/subscription` endpoint — **it
  lags**, and a stale read misled me once; re-query if a number looks wrong.
- **Total API spend for the day: well under $2.** Cost is not the constraint;
  the instrument is.

---

## 9 · Standing rules for whoever picks this up

- **Never fabricate outputs, transcripts, scores or costs.** Never count
  stubbed output as working generation.
- **Pause for explicit approval before `git commit`, `git push`, or deploy.**
  Local verification is always fine.
- Omit the `Co-Authored-By` trailer from commit messages.
- PRs target `develop` in the *other* repo; this repo's convention is
  feature branch → `main` via PR, and `main` is the manager's.
- `../ai-studies-console` and its `genmedia-eval/` are read-only history. The
  voice lane lives here now; four old runs there were never migrated.

---

## 10 · Immediate next steps

1. **Two full passes are running** (`--label p1`, `--label p2`) so every
   scenario lands with a noise floor. When they finish: render the dashboard
   and read the Repeats tab *before* quoting any gap.
2. **Commit the uncommitted work** listed in §1.
3. **Get OpenAI credit (~$5) and cross-check the judge** exactly as the ASR
   was cross-checked. Highest-value remaining experiment.
4. **Build speaker cosine** — verified feasible, unlocks 5 scenarios, and
   completes the "same voice across a batch" half of two claims
   `vr-game-01` and `vr-game-02` already make but cannot support.
5. **Fix ADC properly** so runs stop depending on a 1-hour token.

---

## 11 · The lesson worth carrying

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
