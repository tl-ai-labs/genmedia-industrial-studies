# Pre-run review — video lane, edits + ads bank
**Reviewed 2026-09-10 13:00 IST · reviewer: Claude Opus 5 · gate: DO NOT RUN YET**

Scope: `scenarios/bank-video-pending`, 18 in-scope cells (8 `video_edit`,
10 `image_to_video`/`text_to_video` ads) x 2 arms = 36 cells. Target per the
study lead: both arms at 1080p.

Verdict: **four blockers and four major findings. Two of them invalidate the
comparison rather than merely costing money, and one of those also applies
retrospectively to the 14-scenario pilot.**

---

## BLOCKERS

### B1 · Blind judging is not blind — the clips name their own vendor
Severity: HIGH · Invalidates the result, not just this run

Both providers embed C2PA provenance in the mp4 container. Measured on the
pilot outputs:

| Arm | Embedded in the container |
|---|---|
| Seedance | `BytePlus_ModelArk`, `dreamina-seedance-2-5` (the exact model id), `Byteplus Pte. Ltd.`, `certificate@byteplus.com` |
| Omni | `Google LLC`, `Google C2PA Media Services`, `encoder=Google`, `pki.goog` |

`runner/judge.py` sends video **byte-for-byte** — the code says so and records
`media_stripped: false` on the judge row, so the caveat is documented. What was
not known is that the payload contains the model id in plaintext. The judge is
Gemini, reading mp4 natively, scoring a Google-vs-BytePlus comparison.

**This applies to the pilot too.** The 9.26 vs 9.27 tie was produced under the
same conditions.

**Fix (verified on the pilot files, lossless, no re-encode):**

    ffmpeg -map 0:v -c copy -map_metadata -1 -map_chapters -1 -fflags +bitexact

Removes the `uuid`/C2PA box; the remuxed files contain only `ftyp/free/mdat/moov`.
Residual `strings` hits are random bytes inside the compressed bitstream and
appear in both arms' files equally. Originals stay untouched; only the judge's
copy is cleaned.

### B2 · Audio presence is a second tell, and an unfair axis
Severity: HIGH

Omni's pilot output carries a `SoundHandler` audio stream. Seedance's does not:
`audio: false` is forwarded to Seedance as `generate_audio: false`, while Omni
lists it in `params_unsupported` (no audio toggle on the Interactions surface).

No rubric criterion grades audio, so it contributes nothing but a label. The
`-map 0:v` in B1's fix drops it for judging and closes both problems at once.

### B3 · `est_usd_per_call` is a 720p figure on a 1080p run
Severity: HIGH · Defeats the new per-provider cap

`models.yaml`: Seedance `1.87`, Omni `0.81` — both 8s @ 720p. At 1080p the
pre-flight computes ~$34 for work that actually costs $70-97, so it waves
through a plan no cap can cover and leaves only the mid-run guard. The config's
own comment already says to set `4.55` for a 1080p run. Omni's 1080p figure is
now measured: `$0.1518/s` from the 2026-09-09 probe (49,233 output tokens for
5.675s) -> ~`1.22` for an 8s clip.

### B4 · Every arm is disabled
Severity: BLOCKER by design

`enabled: false` on all four. Flipping it is the study lead's decision, never
the runner's. Nothing can bill until it changes.

---

## MAJOR

### M1 · 1080p on the edits penalises the model that behaves correctly
Severity: HIGH · This is a decision, not a bug

**All eight edit sources are 1280x720.** `scoring.technical_compliance_score`
scores delivered dimensions as `min(1, w/target_w, h/target_h)`. With
`resolution: 1080p` declared, a model that returns 720p — matching its source,
which is what "everything else must stay exactly as it is" implies — scores
**6.67/10**, while a model that upscales scores **10**. The probe already shows
Omni upscaling to 1920x1080 with `scale_ratio: 1.5`.

So 1080p on the edits pays 2.4x per second for invented detail and then rewards
it. Recommendation: **720p on the 8 edits, 1080p on the 10 ads** (the ads are
image-fed, with no source resolution to preserve). Costs ~$21 instead of ~$51
for the edit half, and matches what the pilot and the Artificial Analysis board
used.

### M2 · Right now the edits declare no resolution at all
Severity: MEDIUM

With no `resolution` and no duration band in `checks:`, `technical_compliance`
returns `None` for all 8 edits — the criterion is dropped and its 0.15 weight
silently redistributed across the judged criteria. The edits and the ads are
therefore scored on **different effective weightings**. It is recorded in the
manifest, but it is not intended. Declaring a resolution fixes it.

### M3 · Omni's recorded cost is a floor, not a total
Severity: MEDIUM · Biases the headline

Omni's price block declares only `usd_out_per_1m`. `cost.py` adds input tokens
only when an input rate exists, so input tokens are counted in telemetry and
priced at zero. The probe recorded **31,815 input tokens** on a single edit —
65% of that call's output tokens. All 18 in-scope scenarios are asset-fed.

Cost appears in the client report. Until an input rate is declared, Omni's
figure understates it and the comparison flatters the Google arm.

### M4 · Ten of eighteen scenarios use a code path that has never run
Severity: MEDIUM

`image_to_video` has never executed on either arm. Seedance `video_edit` has
never executed at all — the 2026-09-09 probe was refused at the door with
`AccountOverdueError`. Omni carries `_assert_assets_carried` to catch a
silently dropped asset; **Seedance has no equivalent guard**, so a malformed
data URI that the API tolerates would produce a prompt-only clip that looks
like a success.

Recommendation: a canary of 1 edit + 1 ad on both arms (4 cells, ~$8) before
committing the rest.

---

## MINOR / NOTED

| # | Finding |
|---|---|
| N1 | **No `seed` on any scenario.** Both adapters accept one. Without it the run is not reproducible and a re-run cannot be compared to this one. |
| N2 | **Provider-side failures are invisible to our ledger.** A failed Seedance task returns no usage, so nothing is billed in telemetry. If BytePlus bills for it anyway, only the CSV bill export will show it. |
| N3 | `configs/models-probe.yaml` is committed with `seedance-2-5: enabled: true`. Only reachable via `--models`, but it is a live arm sitting in the repo. |
| N4 | **VID-EDIT-06** forces `duration_s: 8` over a 6.0s source, overriding the adaptive default. Deliberate (the brief says "extend to eight seconds"), but `duration_preserved` will read false — a measure, not a gate. |
| N5 | Payloads are fine: largest base64 asset 5.42 MB (VID-EDIT-08). Disk fine: 668 GB free, run output ≲1 GB. |
| N6 | Pacing: Omni is capped at `rpm: 2` (one call / 30s) and `max_concurrency: 1`, so it is the long pole — ~18 min of pure pacing for 18 cells. Seedance allows 3 concurrent. Use `--workers 4`. |
| N7 | Cell timeout is 900s with 10s polling. Untested against a 19.2s 1080p edit (VID-EDIT-08). |
| N8 | **IST requirement already satisfied**: the host runs at UTC+5:30, so run ids (`2026-09-10_HHMMSS_video`) are already IST. Model-wise layout comes from `runner.cli index` -> `outputs/by-model/<model>/`. |

---

## Recommended order

1. Apply B1 + B2 (strip metadata and audio for the judge's copy only).
2. Apply B3 (1080p estimates) and decide M1 (edit resolution).
3. Add a seed (N1) if the run is meant to be reproducible.
4. Canary: 1 edit + 1 ad, both arms, `--budget 12 --budget-provider byteplus=8`.
5. Read the canary's telemetry: real 1080p rate, real token tier, assets bound.
6. Size and launch the remainder from measured numbers.
