# Run observations: Omni Flash vs Seedance 2.5 (video, 18 scenarios, 2026-09-10/11)

## Overall
- Of the 18 scenarios, 11 were compared: **Omni Flash won 4, 2 were ties, Seedance 2.5 won 5**. The other 7 were not compared because one or both models failed.
- **Neither task has an overall winner.** Quality is close, and each model wins on different scenarios.

## Image to video (10 ad scenarios)
- On the 8 scenarios both models completed, **Seedance averages slightly higher** (93.9% vs 91.6%). It won 5 of the 8 and Omni won 3.
- **Seedance keeps the product closer to the reference image** (97.9% vs 87.1%). Omni often changes small label text, such as the pack weight or lower label lines.
- **Omni follows the prompt slightly better** (94.3% vs 91.4%). Both struggle with hands: fingers morph or slide on AD-02 (Seedance) and AD-04 (Omni).
- **Seedance refused 2 ads** (AD-05, AD-08) because its own generated audio was flagged as copyrighted. Omni completed all 10.

## Video edit (8 scenarios)
- **Only 3 of 8 could be compared.** Omni won background replacement (EDIT-03: 90% vs 82%, cleaner edges round the person). EDIT-05 and EDIT-07 tied at 100%.
- **Omni failed 4 edits** (EDIT-04, 06, 08, 09) with "could not generate the video".
- **Seedance refused 2 edits** (EDIT-01, EDIT-09) because the input clip shows a real person.
- **Seedance's EDIT-04 and EDIT-08 failed because it couldn't download the source clip.** That's a file-hosting problem on our side, not the model, so both should be re-run.
- **Seedance's only edit that Omni didn't complete scored 54%** (EDIT-06, extend a clip). There is a visible jump and a quality drop where the added footage starts.

## Speed and cost (internal only; not in the client report)
- **Omni is 2–4× faster.** Median time per clip was 71 s vs 303 s on image to video, and 99 s vs 222 s on edits.
- **Omni is 3–4× cheaper per clip.** It cost $1.34 vs $3.76 on image to video, and $1.27 vs $5.67 on edits.

## Caveats
- **The sample is small:** one generation per scenario, and only 11 scenarios compared on both sides. Treat per-scenario results as indicative, not conclusive.
- **Scores come from one AI judge** (Gemini 3 Flash) that doesn't know which model made each video. A scenario is a tie only when both scores are identical.
