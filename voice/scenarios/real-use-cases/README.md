# Real-use-case voice bank

Eight scenarios, two per industry, built from the **"Voice — real use cases"**
tab of `updated_scenarios.xlsx` (28 scenarios, seven per industry, each
anchored to a named deployment with a source).

Every scenario here is anchored to something a named company is already
doing. If nobody is doing it, it is not here.

## The framing, because it decides how these are written

The goal is **to find where Google's models are genuinely useful** — not to
show that Google is best, and not to build a case that says so. Two
consequences, and they are structural rather than aspirational:

1. **Every claim is written narrowly.** "Our model does *this job* well",
   never "our model is best". A claim scoped to one job survives contact
   with a customer who tests it.
2. **Every decisive gate can fail Google.** The digit check, the slot
   tolerance, the ACX window and the numbering gate are all binary and all
   model-blind. Three of the eight target *claimed* Google strengths — which
   makes them tests of those claims, not demonstrations of them.

If a comparison comes out against Google on one of these, that is the
finding, and it is worth more to a seller than a favourable number they
cannot defend. The workbook's own instruction stands: **pair each claim with
a scenario where we lose.** A seller who volunteers one weakness gets
believed on the other six.

## Why these eight, out of twenty-eight

The workbook's diagnosis of the earlier flat result is correct: naturalness
has saturated, so "read this passage naturally" measures a dimension where
the field has converged. Every scenario here instead has a **binary or
measurable failure** — a digit is right or wrong, a read fits 15.0s or it
does not, an amount is in the Indian system or it is not.

These eight are the subset of the 28 whose headline measurement this harness
can actually make today. The other twenty are listed at the bottom with what
each one needs; several are more valuable than what is built here, and none
of them are stubbed in as if they worked.

| ID | Industry | Real deployment behind it | The decisive gate | Sales claim if it passes |
|----|----------|---------------------------|-------------------|--------------------------|
| `vr-ecom-01` | Ecommerce | WISMO — >60% of "where is my package" calls are AI-handled | 12-digit reference read back exactly | Highest-volume call type, right first time |
| `vr-ecom-03` | Ecommerce | Returns/refunds — 2nd most automated retail voice flow | `Rs 2,50,000` read as **lakh**, not hundred-thousand | Indian numbering spoken natively |
| `vr-drama-02` | Micro-drama | Audible/ACX now permit AI narration to spec | RMS −23..−18 dBFS, peak ≤ −3, floor ≤ −60 | Lands in Audible's spec with less mastering |
| `vr-drama-04` | Micro-drama | Micro-drama localisation must fit the original cut | Trimmed read within ±0.15s of an 11.0s shot | Hits shot timing without re-cutting |
| `vr-game-01` | Gaming | ARC Raiders ships TTS callouts, pings, briefings | Grid digits + five invented proper nouns | Full callout set in one pass, intelligible |
| `vr-game-02` | Gaming | THE FINALS shipped an AI announcer | Final score exact, no dead air, no clipping at climax | Announcer lines the day the feature ships |
| `vr-ads-02` | Ads | Fixed broadcast slots; 2,400+ variants per quarter | Trimmed read within ±0.1s of 15.0s | Fifteen seconds means fifteen seconds |
| `vr-ads-06` | Ads | Regulated categories need fast, intelligible legal copy | ≥190 wpm **and** WER ≤ 0.06 at that pace | Fast legal copy that stays intelligible |

`vr-ads-06` is the only scenario whose two gates pull against each other on
purpose: the copy has to be fast *and* intelligible, and most quality metrics
cannot see that trade-off at all.

## Three of these target stated Google strengths

Named as such so nobody mistakes a test for a demonstration:

- **`vr-ecom-03` — Indian numbering.** Local-language handling was named as a
  competitive advantage, and Western-trained models routinely default to
  international numbering. The script sends the **numeral** `Rs 2,50,000`, so
  the model has to decide how it is said. Both Indian readings pass; only the
  international one fails.
- **`vr-ecom-01` / `vr-ads-02` — India-context delivery.** Indian courier,
  city and brand names, and Indian-market ad copy, in scenarios whose gates
  are about something else. Pronunciation failures show up without the
  scenario being *about* pronunciation.
- **`vr-game-02` — turnaround on announcer lines.** The shipped precedent is
  Embark's, not Google's; what is measurable here is whether the output is
  usable, not who got there first.

The strongest Google story in the whole workbook — **`vr-game-03`, real-time
conversational NPCs, where Fortnite's AI Darth Vader already runs on Gemini
2.0 Flash** — is *not* in this bank, because its headline measure is
time-to-first-audio and this harness has no streaming path. That is the
single highest-value thing to build next. See below.

## Running it

```bash
.venv/bin/python -m runner.cli --modality voice --scenarios scenarios/real-use-cases run --budget 1.00
```

**Mind the ElevenLabs quota.** The eight scripts total **2,704 characters**,
which is what one pass sends to the ElevenLabs arm. Measured against the
account on 2026-09-03: **4,061 characters remaining** of a 10,000 free-tier
allowance, resetting 2026-10-01. **One pass fits; two do not** (5,408). On a
free tier the binding constraint is quota rather than money — the $0.27 the
report shows for that arm is the list rate, not a charge. Note also that these live under `scenarios/`, so a
bare `run` with no `--scenarios` now picks up **all fourteen** scenarios, not
just these eight. Pass `--scenarios` deliberately.

## What each scenario does NOT cover

Stated per file too, in the header comment. The pattern is the same in each
case: the deterministic gate is real, and a condition the source scenario
specifies is missing, so the result is a **ceiling rather than a field
measurement**.

- `vr-ecom-01` — no 8kHz telephony codec, no call-centre background noise
- `vr-game-01` — no combat audio bed; intelligibility is measured clean
- `vr-game-01` / `vr-game-02` — the "one consistent voice across a batch"
  half of both claims needs speaker cosine and is **not** measured
- `vr-drama-02` — ACX also requires human review, ≤120 min/file and AI
  disclosure at upload; none are audio properties. The passage is a
  representative segment, not the specified five minutes
- `vr-drama-04` / `vr-ads-02` — no rate control: only the OpenAI adapter
  honours `params.speed`; ElevenLabs and Gemini ignore it and report it in
  `params_unsupported`. These measure **natural** delivery length against a
  slot, and both models may miss. The signed deviation in
  `measurements.trimmed_duration_s` still ranks them

## The twenty not built, and what each needs

Ranked by how many scenarios one capability would unlock.

| Capability needed | Unlocks | Notes |
|---|---|---|
| **Speaker-embedding cosine** | 9 — `DRAMA-01`, `DRAMA-03`, `GAME-04`, `GAME-06`, `ADS-01`, `ADS-03`, `ADS-04`, plus the second half of `GAME-01` and `GAME-02` | By far the highest leverage. Every "same voice across episodes / languages / a 50-line batch" claim is blocked on this one number |
| **Streaming + time-to-first-audio** | 3 — `GAME-03`, `GAME-05`, `ECOM-02` | Includes the strongest Google story in the workbook. Game audio teams cite 300 ms as the immersion threshold; the harness currently records whole-call latency, which is **not** TTFA and must not be reported as it |
| **Blind human listener panel** | 3 — `DRAMA-07`, `ADS-04`, `ADS-07` | Adjacent-emotion labelling. Related: the project's own calibration gate (2 humans × 5 clips) has never been run |
| **Per-language segment WER + native review** | 4 — `ECOM-05`, `ADS-03`, `GAME-06`, `DRAMA-03` | Hindi-English code-switching. Needs segment alignment, not just a whole-clip WER |
| **Telephony codec + noise simulation** | 2 — `ECOM-01` (full form), `ECOM-06` | 8kHz band-limiting is cheap to add and would upgrade an existing scenario rather than only unlocking new ones |
| **Character-exact alphanumeric readback** | 1 — `ECOM-06` | Confusable pairs (B/8, Q/O, Z/2) and a confusion matrix by character class. The digit checks exist; letters do not |
| **Diarisation** | 1 — `DRAMA-06` | Multi-character scene, line-to-speaker mapping |
| **Far-field / band-limit simulation** | 1 — `DRAMA-05` | Smart-speaker listening condition |
| **Phoneme alignment vs a pronunciation guide** | 1 — `ADS-05` | ASR `must_say` is a usable proxy today but is not phoneme comparison, and should not be described as one |
| **Batch throughput reporting** | 3 — `ECOM-07`, `GAME-07`, `ADS-01` | Cost and latency are already recorded per call; this is a run-level report, not new measurement |
| **Conversational loop / barge-in** | 1 — `ECOM-02` | Not a TTS capability at all — needs a duplex agent |

## The guard on this directory

`tests/test_scenario_bank.py` takes every scenario's own script as a
hypothetical perfect transcript and asserts every deterministic check would
pass, that no `must_not_say` phrase is already in the script, that digit
gates are recoverable, and that duration and rate targets imply a humanly
possible read.

It exists because of a real failure: on 2026-09-02 a scenario sent "sixteen
gigabytes of RAM", the ASR wrote "16 GB", and four checks failed on **both**
models identically — an instrument fault wearing a model fault's clothes. A
check that cannot pass a perfect reading is not strict, it is broken.
