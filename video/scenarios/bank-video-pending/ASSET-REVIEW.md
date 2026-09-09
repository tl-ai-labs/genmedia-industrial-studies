# Asset review — video_edit and image_to_video (2026-09-09)

Every wired asset was opened and compared against the prompt it serves,
before any run. Seven of nine edit clips and all the ad references are sound.
The exceptions are recorded here rather than discovered mid-run.

## Blocking — needs a decision

**VID-EDIT-02 "Remove a background element" — the asset does not fit the brief.**

The prompt is *"Remove the parked bicycle from the background of this clip.
Reconstruct what is behind it. Do not change the foreground subject or the
camera move."*

The clip is a busy pedestrian street where **dozens of bicycles fill the
entire foreground, out of focus**, with the crowd behind them. So:

- there is no single identifiable *"the"* parked bicycle;
- the bicycles are the **foreground**, not the background;
- the "foreground subject" the model is told to preserve *is* the bicycles.

Whatever a model returns cannot be scored: the referent is ambiguous, so the
judge cannot say whether the right object was removed. This scenario would
produce noise, not signal.

Options: source a clip with one clearly parked bicycle behind a subject, or
retire the scenario. The prompt is verbatim from the sheet and should not be
rewritten to fit the asset we happen to have — that would be fitting the test
to the data.

## Fixed

**VID-AD-06 "Vertical variant"** — the brief asks for 9:16 vertical; the
scenario was wired `aspect_ratio: 16:9`, which made it unsatisfiable by
construction. Now 9:16. (Ours to set — the sheet's run settings, not the
prompt.)

**VID-EDIT-06 "Extend clip duration"** — an edit otherwise keeps the source's
length (`ratio: adaptive`, `duration: -1`, per the API reference's own edit
example). This is the one edit that must *grow*, so `duration_s: 8` is now
explicit. Without it the adapter would have told the model to keep the length
while the prompt asked to extend it.

## Noted, not blocking

**Source clips run long** — 19.2s (EDIT-02, EDIT-08), 16.8s (EDIT-05),
11.6s (EDIT-09), 11.2s (EDIT-03). Seedance bills per **output token**, so an
edit that returns the full source length costs proportionally more than an 8s
clip: a 19.2s return is roughly 2.4x. Budget for the edit set on measured
source lengths, not on an 8s assumption.

**VID-EDIT-06's prompt says "this five-second clip"; the asset is 6.0s.** The
prompt is verbatim from the sheet, so it is left alone, but the brief states
something false about its own input. Worth a note to the judge if the model
is penalised for the join.

**Six scenarios share one reference still** (VID-AD-01/02/03/06/07 and
VID-I2V-05 — the AURELO CACAO jar). This is deliberate and sound: one product
across five ad treatments is a cleaner comparison than five different
products, because it holds the subject constant while the treatment varies.

**VID-EDIT-04 carries a second, non-video data stream.** Harmless for
playback and for our parser, which reads the video track, but worth knowing
if a provider rejects the container.
