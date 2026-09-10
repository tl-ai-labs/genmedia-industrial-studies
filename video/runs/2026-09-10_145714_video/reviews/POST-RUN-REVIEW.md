# Post-run review — 2026-09-10_145714_video (ads, 1080p)

**Run started 14:57:14 IST, finished ~15:42 IST. State: `generated`.**
Scope: 10 ad scenarios x 2 arms = 20 cells, 1080p, audio ON, seed 20260910.

## Outcome

| | |
|---|---|
| Cells measured | **16 / 20** |
| Cells failed (provider refusal) | 2 — both Seedance |
| Cells invalid (our own gate) | 2 — VID-AD-06, both arms |
| **Usable pairs for comparison** | **7 / 10** |

| scenario | omni | seedance | pair |
|---|---|---|---|
| VID-AD-01..04, 07, 09, 10 | measured | measured | **YES** |
| VID-AD-05 | measured | failed (refused) | no |
| VID-AD-06 | invalid | invalid | no |
| VID-AD-08 | measured | failed (refused) | no |

## Cost — and a $29.27 discrepancy

| | |
|---|---|
| Telemetry recorded, BytePlus | $37.64 (9 billable generations) |
| **ModelArk actually billed today** | **$66.91** (16 succeeded tasks) |
| **Unrecorded** | **$29.27 — 7 orphaned clips** |
| Google/Vertex (Omni), recorded | $13.40 (11 generations) |
| **Balance remaining** | **~$24.91 of $91.82** |

Every Seedance task bills **390,825 completion tokens = $4.1818**, invariant
across all 16. Omni bills **$1.2180** per ad, invariant across all 11. The
cost model is now exact for both arms.

The **28% 1080p promo is NOT reflected in token billing**. Confirm against
the CSV bill export before assuming it applies at invoice time.

## F1 · Audio caused two refusals — 20% of the Seedance arm

```
VID-AD-05  refused  266s  OutputAudioSensitiveContentDetected.PolicyViolation
VID-AD-08  refused  290s  "the output audio may be related to copyright restrictions"
```

Both generated for 4-5 minutes, then had their **output audio** blocked. With
`audio: false` — the setting before 2026-09-10 — neither would have been
refused. Enabling audio bought a symmetric comparison (it removed the
audio-presence blinding tell) and cost two scenarios.

Refusals bill nothing: both show `failed`, 0 tokens.

**Decision needed:** keep audio and accept ~20% Seedance attrition, or return
to `audio: false` and re-introduce the blinding tell that B2 closed. A third
option is audio off for both arms, which is symmetric AND avoids the refusals.

## F2 · VID-AD-06 was failed by an impossible gate we wrote

Both arms delivered **exactly 1080x1920**, matching `target_width/height`
precisely — the correct 9:16 vertical the brief asked for. Both were then
marked `invalid` by the scenario's own gate:

    checks: {min_width: 1280, min_height: 720}   # a LANDSCAPE floor

A portrait 1080p clip is 1080 wide. 1080 < 1280, so the gate can never pass.
The `aspect_ratio` was corrected to 9:16 on 2026-09-09; the checks block was
left behind.

Worse, `invalid` triggers one regeneration, so we paid **twice on each arm**:
~$8.36 Seedance + ~$2.44 Omni = **~$10.80 spent proving our own gate wrong**.

**Fixed** in all three scenario copies: `min_width: 720, min_height: 1280`,
with a comment explaining that the short side is the width in portrait.
The run itself stays immutable — VID-AD-06 needs a fresh run to be scored.

## F3 · The create-stall is NOT fixed by a longer timeout

Four creates died with OS-level `[Errno 60] Operation timed out` at
**87s, 97s, 122s, 131s** — all well inside the new 600s HTTP window. Raising
120s -> 600s did help the merely-slow creates (successes landed at 262-500s),
but these are TCP stalls during the upload of a multi-MB base64 data URI,
not deadline expiries.

Omni receives the same assets as base64 with zero failures, so this is
ModelArk-specific.

**Every stalled create still produced and billed a clip.** The non-retryable
change (F4) stopped the retry storm, but one stall still equals one orphan.

**Real fix:** stop inlining assets. ModelArk accepts `image_url` with an HTTP
URL, which makes the request body tiny and removes the stall entirely. That
is the change to make before the edits run — the edit sources are 1.0-4.1 MB
each, larger than these ad stills.

## F4 · The non-retryable change worked

VID-AD-03 timed out at 97s, was NOT retried, and later completed cleanly on
its own cell attempt for a single charge. Under this morning's behaviour that
same event produced five paid clips for two logged failures.

## F5 · Performance

| arm | n | min | median | max | total |
|---|---|---|---|---|---|
| Omni Flash | 11 | 50s | **71s** | 78s | 12.5 min |
| Seedance 2.5 | 9 | 216s | **303s** | 500s | 46.5 min |

Seedance is **4.3x slower** per clip at 1080p. With concurrency 3 vs Omni's
serial `rpm: 2`, wall clock came out near parity — but Seedance is the cost
and time driver either way.

## What needs doing

1. **Reconcile against the BytePlus CSV export** for 2026-09-10 — confirm
   $66.91, and whether the 1080p promo applied. (F-cost)
2. **Decide the audio policy** (F1) before any re-run.
3. **Switch Seedance assets to hosted URLs** (F3) before the edits run.
4. **Re-run VID-AD-06** now that its gate is fixed — 1 pair, ~$5.40.
5. **Judge the 7 usable pairs** — cheap, Google-billed, ~$0.15.
6. Top up BytePlus before the 8 edits: they need ~$47 at 1080p and only
   ~$24.91 remains.
