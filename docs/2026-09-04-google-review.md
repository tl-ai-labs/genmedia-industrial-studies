# Google weekly review — 4 September 2026

> **Status: the video lane is PAUSED as of 2026-09-04.** Every arm in
> `video/configs/models.yaml` is `enabled: false`, so a run is rejected at
> pre-flight and nothing can bill. Resuming means a deliberate flip of one arm
> plus a `--budget`. Nothing below has been actioned except the metric rename
> in §3.

Attendees: Ravindar Katkuri (Tilicho), Chom Trevai, Pranav Mehrotra, Gaurav Kumar (Google).
Sources: call summary + full transcript, `~/Downloads/Google Call 4th Sep, 2026`.

This is the context note for the study. It records what the call decided, and — the
part that matters for the next run — what each decision actually changes in this
repo. Two of the decisions invalidate work we have already shipped. Those are called
out first, because they change the story we tell, not just the code.

---

## 1. The image comparison we presented is at the wrong tier pairing

Gaurav's correction: the three Gemini image tiers map to three GPT tiers, and that
mapping is how the industry compares them.

| Gemini | GPT Image 2 | Price point quoted on the call |
|---|---|---|
| Nano Banana **Pro** | **high** | Pro 14¢ |
| Nano Banana **Flash** | **medium** | GPT-2 medium 6¢ |
| Nano Banana **Lite** | **low** | GPT-2 low 2¢ |

> "So if you compare Gemini 3 Pro with GPT 2 medium, then there are different price
> points… the comparison is not valid." — Gaurav
> "Because high is very good. So if you say Gemini Pro is better than GPT 2 high, it
> raises eyebrows."

**The 46-scenario run we showed is Pro vs `gpt-image-2-medium`.** That is Pro against
the *middle* tier — the invalid pairing.

**We already have the valid pairing on disk**, and it tells a materially different
story. Run `2026-09-01_224335_image`, 14 scenarios, Pro vs `gpt-image-2-high`:

| | Pro vs **medium** (46 scen, presented) | Pro vs **high** (14 scen, valid pairing) |
|---|---|---|
| Gemini rating | 94.7% | 97.3% |
| Rival rating | 93.5% | **98.1%** |
| Gemini reliability (worst) | 70.2% | 77.9% |
| Rival reliability (worst) | 48.0% | **85.0%** |
| Cost per image | $0.134 vs $0.045 (**+201%**) | $0.134 vs $0.168 (**−20%**) |

At the correct tier Gemini is **slightly behind on quality and reliability, and
cheaper** — the opposite of the "higher quality, off-the-chart price" headline that
went to the room. The two runs use different scenario sets and different sample
sizes (14 vs 46), so this is a direction, not a verdict. But the direction is clear
enough that we should not re-present the 46-scenario numbers as a tier comparison.

**What to do:** re-run the 46-scenario bank as Pro vs `gpt-image-2-high` before any
of it is shown again, and add Flash-vs-medium and Lite-vs-low as separate rows.
`gpt-image-2-high` already exists in `image/configs/models.yaml` (parked); Flash
exists (parked, two routes); **Lite does not exist and must be added.**

---

## 2. The video pilot we just ran is now out of scope

> "don't waste too much time. Just focus on video edits." — Gaurav
> "ads, another example, because ads doesn't need all of that fancy movie sequences."
> "video, I don't have much hope on because if you go to studios type of examples."

Decision recorded: **video scoped to video edits and ads only; studio-grade
sequences dropped.**

Our 14-scenario pilot was Cinematic/hero shot + Physics & action — precisely the
studio-grade material now dropped. The pilot stays valid as evidence that the
harness works and as a Seedance cost/latency baseline, but it is no longer the
story.

The in-scope families and their state today:

| Family | n | task | Runnable now? |
|---|---|---|---|
| Conversational / local video editing | 10 | `video_edit` | **No** |
| Advertising & product video | 10 | `image_to_video` (9), `text_to_video` (1) | Only VID-AD-09 |

**The blocker is real work, not configuration.** Every video arm declares
`supports: [text_to_video]` only, and `lifecycle.py` builds a task spec for
`text_to_video` alone — `image_to_video`, `video_edit` and `avatar_dialogue` are
registered as reserved. So of the 20 newly in-scope scenarios, exactly **one** can
run today.

The inputs are not the blocker: the asset bank is already built and wired — 31
stills and 9 `VID-EDIT-0x-source.mp4` clips, each with a provenance sidecar.

**What to do, in order:** extend the Omni and Seedance adapters to `image_to_video`
and `video_edit`, flip `supports`, then run the 20. Until that lands there is no
in-scope video comparison to show.

---

## 3. Renamed metric

> "Worst scenario rating is great, but call it reliability… net-net this is
> performance consistency, or performance reliability. It's a good metric, I can
> sell this metric." — Pranav

`worst` is now labelled **"Reliability (worst scenario rating)"** in both lanes,
in the comparison table and the head-to-head strip. Done.

---

## 4. Industry mapping changes

Ads is **not** a top-level category. It is a sub-use-case of e-commerce / retail
(product placement).

> "So ads… I would classify that within the e-commerce and retail space."
> "within e-commerce, one use case could be virtual try-on. Second use case could be
> ads or product placement."

This is a live problem for the video lane: **30 of the 60 video scenarios currently
have `Ads` as their primary industry** — half the bank sits under a category that no
longer exists at the top level. `video/configs/industry_map.yaml` needs remapping to
E-commerce & Retail with Ads as the sub-use-case.

The four segments Google actually sells to, for use-case coverage:

1. **B2C aggregators / apps** — Canva, Higgsfield, Fal; they resell the API.
2. **E-commerce & retail** — virtual try-on (catalog→mannequin, catalog→person),
   catalog image enrichment, product ads / placement.
3. **Gaming** — pre-development assets (environments, characters) and in-play asset
   generation.
4. **Studios** — spatial awareness (move the camera, distances must hold), lighting
   consistency, photo-shoot background consistency; animation, incl. Korean/Japanese
   studios. Style transfer / stylisation sits here too (OEM sketch-to-image).

---

## 5. Where Gemini is winning today (the seed for the next scenario set)

From the 46-scenario image run, wins concentrate in **e-commerce / retail**:

- **Logo placement on packaging** — perspective and geometry on a box face.
- **Multi-SKU stitching** — several product shots combined at correct relative scale.

These are the two the room reacted to, and they are the shape to expand on: an
input-asset-driven edit with a checkable geometric outcome. Note the caveat in §1 —
these wins are against *medium*, and need re-confirming against *high*.

---

## 6. Other findings, not ours to action this week

- **Voice**: TTS latency 7.5s lags 11 Labs, but that is total time — retest in
  **streaming mode** (time-to-first-token is 600ms–1s). We are comparing against
  **11 Labs v2; the competitor is v3.** Restructure voice into call centre
  (retail / telco / banking terminology) and micro-drama (mixed Hindi-English,
  emotion). Known strengths: language and pronunciation, emotion. Known concern:
  voice drift across sequences — affects both us and 11 Labs.
- **Human ratings**: consolidate 60–70 blind reviewers into one internal site
  alongside the LLM-judge scores. Chom's point: the judge cannot hear a mispronounced
  brand name; a human can.
- **Synthetic data methodology**: document how the scenario bank was generated and
  sourced, to answer the bias question from prospects before it is asked.

---

## 7. Open questions

1. §1 says re-run 46 scenarios as Pro vs high. That is real spend — needs a budget
   decision before it starts.
2. Does the Lite-vs-low row need the full bank, or a subset sufficient to make the
   price argument?
3. For video, is the priority `video_edit` (Gaurav's "we know we do well") or the
   ads set? The adapter work differs: edits need a source clip, ads need a source
   still.
