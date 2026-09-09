# Task split — week of 9 September 2026

Follows the 4 September Google review (`docs/2026-09-04-google-review.md`).
Owners as assigned by Ravi. Each task says what already exists, so nobody
rebuilds something that is already here.

---

## 1 · Image tier mapping — **Gaurav**

Three paired runs, because Google compares tier-for-tier and our published
comparison does not.

| Pair | Gemini arm | GPT arm | State today |
|---|---|---|---|
| **A** | Nano Banana **Pro** | GPT-2 **high** | Both configured. `gpt-image-2-high` is parked — flip `enabled` |
| **B** | Nano Banana **Flash** | GPT-2 **medium** | Flash configured on two routes; GPT-2 medium enabled |
| **C** | Nano Banana **Lite** | GPT-2 **low** | **Neither exists — both blocks must be written** |

**Order of work**

1. **Pair A first.** It is the one that corrects a number already shown to
   Google. Re-run the 46-scenario bank as Pro vs `gpt-image-2-high`.
2. **Pair B.** Use the **Vertex** Flash route (`gemini-3-1-flash-image-vertex`),
   not the API-key one — that key's image quota is 0.
3. **Pair C.** Add a `gemini-3-lite-image` (or current id) block and a
   `gpt-image-2-low` block to `image/configs/models.yaml`, then run.

**Also in this bucket:** confirm and re-label which GPT tier every past run
used. Two runs exist and they disagree — `2026-09-02_011110_image` (46 scen)
sent `quality: medium`, `2026-09-01_224335_image` (14 scen) sent
`quality: high`. Anything already shared from the 46-scenario run needs the
tier stated on it.

> **Budget gate.** Three paired runs over the 46-scenario bank is real spend.
> Ravi to approve a figure before Pair A starts.

---

## 2 · HTML generation — client first, internal second

**Image and video are done.** Every report build already writes both files
from one context, so they cannot disagree:

```bash
python -m runner.cli report --run <run-id>              # writes both
python -m runner.cli report --run <run-id> --self-contained   # video: embeds clips
```

- `report-client.html` — percentages, Gemini on the left, Difference column,
  cost per image/clip, no internal diagnostics.
- `report.html` — everything, unchanged.

**What is actually left here:**

1. **Run the reports for each new tier pair** as they land (task 1). Client
   file first, since that is what goes out.
2. **Align the voice dashboard** to the same conventions. Voice has its own
   exporter (`voice/dashboard/`, deployable, 242 clips committed) and it
   predates the image/video split — it needs the client/internal separation,
   Gemini-left ordering, percentages, and the "Reliability (worst scenario
   rating)" label so all three lanes read as one study.

---

## 3 · Remaining work — split **Gaurav / Pranav**

### Video — *Pranav*

The lane is **paused**; every arm is disabled and nothing can bill until
someone deliberately re-enables it.

1. **Build `image_to_video` and `video_edit` support** in the Omni and
   Seedance adapters, and lift them out of `lifecycle.py`'s reserved list.
   This is the blocker: of the 20 in-scope scenarios exactly **one** runs
   today. Assets are already built and wired (31 stills, 9 source clips).
2. **Re-map Ads** into E-commerce & Retail as product placement —
   30 of 60 scenarios currently sit under a top-level category that no longer
   exists.
3. Then run the 20 in-scope scenarios (10 edits + 10 ads). Not before.

### Voice — *Gaurav*

4. **Add ElevenLabs v3 and re-run both arms.** We benchmarked v2; v3 is the
   competitor. Every published voice number resets — v2 and v3 results are not
   comparable. Decide the **ElevenLabs-on-Vertex** question first: routing
   through Vertex moves billing to GCP and makes existing cost numbers
   incomparable.
5. **Widen streaming coverage.** Streaming is already built — both adapters
   timestamp the first chunk. Only one scenario sets `max_ttfa_ms`. Add it to
   every scenario where responsiveness is the claim. Real figures are
   2.08s vs 1.29s time-to-first-audio, not 7.65s vs 1.67s whole-call.
6. **Re-segment into call centre and micro-drama**, keeping **retail / telco /
   banking** separate inside call centre.

### Cross-cutting — *Pranav*

7. **Document the synthetic-data methodology** and sources — Chom wants it
   before a prospect asks whether the scenarios are biased or fictional.
8. **Reconcile the cost claim.** We measure Gemini voice ~2.1× cheaper; Chom
   says 4× from Google's own costing. Sellers will quote whichever they see
   first.

---

## 4 · Blind human panel + correlation — **Gaurav**

Chom's requirement: *"that defines whether your AI is aligned to human
judgment."* Also our only instrument for the emotion claim in voice.

**Build on what exists, do not start fresh:**
- Outputs are already exported per run, and `blind_label` is already recorded
  on every judge row — the blinding the panel needs is already in the data.
- `voice/dashboard/` is the pattern to copy: a generated, committed, deployable
  folder. It is display-only today; it captures no input.

**Scope**

1. **UI** — one page per lane. Each item shows the two outputs side by side
   with the model names hidden and left/right order shuffled per item.
2. **Rating** — a single thumbs-up on the better one. Keep it that simple;
   60–70 reviewers will not fill in rubrics.
3. **Storage** — append to a local JSON file, one record per vote:
   scenario id, reviewer id, which side was picked, which model that actually
   was, and a timestamp. No database.
4. **Correlation** — a script that reads the votes plus `scores.jsonl` and
   reports agreement between the human majority and the LLM judge, per lane
   and overall. That number is the deliverable Chom asked for.

**Watch for:** the known disagreement. Our reviewers found Gemini's voice more
soothing while the judge scored it lower. If the correlation is poor, that is a
finding about the rubric, not a bug to hide.

---

## Sequencing

Nothing in §1 starts before Ravi sets a budget. §3's video work is adapter
engineering and can start immediately — it needs no spend. §4 needs no spend
either and can run in parallel with everything.

## One thing to confirm

In the 4 September transcript, Gaurav Kumar and Pranav Mehrotra are the
Google-side attendees. This split assigns implementation work to those names —
please confirm the intended owners before it is circulated.
