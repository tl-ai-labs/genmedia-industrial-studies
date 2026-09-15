# Retired scenarios

Bank rows withdrawn from the runnable set. They stay in the repository —
the bank is 60 rows and the catalogue must keep covering all of them — but
no run will pick them up, and the reason each was retired is recorded here
so it is not rediscovered later.

Retiring is deliberate and reversible. Nothing here is deleted.

---

## VID-EDIT-02 — "Remove a background element"

**Retired 2026-09-09. The brief and the only available asset cannot both be
satisfied, and the mismatch makes the result unscoreable.**

The prompt, verbatim from the sheet:

> Remove the parked bicycle from the background of this clip. Reconstruct
> what is behind it. Do not change the foreground subject or the camera move.

The asset (`assets/bank/VID-EDIT-02-source.mp4`, Pexels, 1280x720, 19.2s) is
a busy pedestrian street in which **dozens of bicycles fill the entire
foreground, out of focus**, with the crowd behind them.

Three separate problems, any one of which is disqualifying:

1. There is no single identifiable *"the"* parked bicycle — there are dozens.
2. The bicycles are the **foreground**, not the background the brief names.
3. The "foreground subject" the model is told to preserve **is** the bicycles.

So no output can be graded. The judge cannot decide whether the right object
was removed, because the brief does not identify one. Running it would spend
money on both arms and return noise that looks like data.

**Why not just reword the prompt?** Because the prompt is verbatim from the
scenario bank, and rewriting it to fit the asset we happen to own is fitting
the test to the data. The scenario is legitimate; our asset is wrong for it.

### To bring it back

Source a clip with **one clearly parked bicycle in the background**, behind a
distinct foreground subject, then:

1. put it at `assets/bank/VID-EDIT-02-source.mp4` with its JSON provenance
   sidecar (source clips are never model-generated — study lead's rule);
2. move `VID-EDIT-02.yaml` back to `scenarios/bank-video-pending/`;
3. delete this section.

The current asset is left in place rather than deleted: it is a valid clip,
just not one this brief can use, and it may suit a future scenario.
