# Pilot run commands — video and image lanes

Date: 17 September 2026 (updated after the third prompt search). Pilot steps 10 to 12
(`docs/2026-09-15-gaming-pilot-bgmi-cricket.md`).
Vertex project for every arm and the judge: **temp-genmedia-study** (ai-studies-console is
suspended for Vertex AI). Credentials: application-default login with the team's Google account.

All commands run from the worktree lane folder with the study's Python:

```
PY=/Users/macbook/Documents/GitHub/Google-Projects/genmedia-industrial-studies/video/.venv/bin/python
```

Before any run, load the API keys and point the quota project at the working project:

```
set -a; . /Users/macbook/Documents/GitHub/Google-Projects/genmedia-industrial-studies/video/.env; set +a
export GOOGLE_CLOUD_QUOTA_PROJECT=temp-genmedia-study
```

## What runs

| Lane | Scenarios | Folder | Arms (from `models-gaming-pilot.yaml`) |
|---|---|---|---|
| Video | 25: 13 image-to-video (BGMI 4, cricket 5, GTA V 4) and 12 edits (4 per game) | `video/scenarios/bank-video-gaming/` | Omni Flash, Seedance 2.5 |
| Image | 21: 13 text-to-image, 8 edits | `image/scenarios/bank-image-gaming/` | Nano Banana Lite, GPT Image 2 low |
| Blocked | none since the third search round; 21 of the 46 cards are Real-based and need the reviewer's approval before the run | `*-gaming-blocked/` (empty) | — |

Inputs come from the generation runs `2026-09-17_101036_image` and `2026-09-17_101040_video`,
copied to `video/assets/bank/gaming/video/` and `image/assets/bank/gaming/image/` under the
sheet's File names. A scenario whose input file is missing is rejected by the loader, so the
run set is only complete once every copy is in place.

## Step 0: generate the eleven missing inputs (needs Sai's go)

Three generation folders are ready and dry-run; all three use the non-evaluated Vertex arms in
`configs/models-gen-inputs-temp.yaml` (Nano Banana Flash for stills, Veo 3.1 for clips):

| Folder | What | Pre-flight |
|---|---|---|
| `image/scenarios/gen-inputs-gaming-gta/` | 5 stills: GTA V 10–13 and the dugout (06) | $0.34 |
| `image/scenarios/gen-inputs-gaming-round3/` | 2 stills: jeep over the rock (01), boundary catch (05) | $0.13 |
| `video/scenarios/gen-inputs-gaming-gta/` | 4 clips: GTA V edits 09–12, 6 s each | $12.80 cap, about $9.60 billed |

```
cd image && $PY -m runner.cli run --modality image --scenarios scenarios/gen-inputs-gaming-gta --models configs/models-gen-inputs-temp.yaml --budget 1.00
cd image && $PY -m runner.cli run --modality image --scenarios scenarios/gen-inputs-gaming-round3 --models configs/models-gen-inputs-temp.yaml --budget 1.00
cd video && $PY -m runner.cli run --modality video --scenarios scenarios/gen-inputs-gaming-gta --models configs/models-gen-inputs-temp.yaml --budget 14.00
```

Then copy the outputs under the sheet's file names and rebuild the sheets (scratchpad scripts):
`node copy-inputs.js 2026-09-17_101036_image 2026-09-17_101040_video <gta-image-run> <gta-video-run> <round3-image-run>`
followed by `node build-pilot.js`.

## Video lane, in order

1. Dry-run (checks files, keys and the estimate; spends nothing). With the inputs that exist
   today, 16 of the 25 scenarios load and the estimate is $140.80; the full set loads once
   step 0 is done:
   ```
   cd video && $PY -m runner.cli run --modality video --scenarios scenarios/bank-video-gaming --models configs/models-gaming-pilot.yaml --budget 0.01
   ```
2. Run. Pre-flight estimate for all 25 is **$216.60**: each image-to-video scenario is $5.40
   a pair (Omni $1.22 + Seedance $4.18) and each edit $12.20 a pair (Omni $1.70 + Seedance
   $10.50, since Seedance bills edits at roughly twice generation). Measured billing on the
   last full run was below the cap: edits about $10.34 a pair, so about $194 is likely (Omni
   Flash about $31, Seedance about $163). Ravi approves the figure:
   ```
   cd video && $PY -m runner.cli run --modality video --scenarios scenarios/bank-video-gaming --models configs/models-gaming-pilot.yaml --budget 220.00
   ```
3. Judge (the judge remuxes every mp4 with ffmpeg to drop the vendor's C2PA box before it
   is shown to the model, so blinding holds; ffmpeg is on this Mac):
   ```
   cd video && $PY -m runner.cli judge --run <run-id> --models configs/models-gaming-pilot.yaml
   ```
4. Report and cost:
   ```
   cd video && $PY -m runner.cli report --run <run-id> --open
   cd video && $PY -m runner.cli cost --run <run-id>
   ```

Expected wall time: about 2 hours for the run (Omni about 70 to 100 s a cell, Seedance about
240 to 320 s, one call at a time per arm), about 25 minutes for judging, under $2 of judge
cost.

Started 17 September at 12:17 with budget 220: run id `2026-09-17_121726_video`.

**Seedance edits need hosted clips.** The Seedance provider fetches a video input itself from
`$ARK_ASSET_BASE_URL/assets/bank/<file name>`, so the 12 source clips must sit at a public URL
before its 12 edit cells can run; stills travel inline and are fine. On the first pass every
Seedance edit cell failed with HTTP 404 at no cost, because the base URL was pinned to an old
repo commit that never held the gaming clips. Sai's decision: keep the clips local, no cloud
copy. Fix used for the pilot: serve the clips from this Mac through a temporary ngrok tunnel
for the length of the resume, then close both. Two terminal tabs, from the worktree folder:

```
mkdir -p /tmp/gaming-serve/assets/bank && ln -sf "$PWD"/video/assets/bank/gaming/video/VID-GEDIT-*-source.mp4 /tmp/gaming-serve/assets/bank/ && $PY -m http.server 8765 --directory /tmp/gaming-serve
ngrok http 8765
```

Check one clip through the tunnel (`curl -I <forwarding-url>/assets/bank/VID-GEDIT-01-source.mp4`
must return 200 and `video/mp4`; a 502 means the file server is not up), then resume the same
run (only cells without an output are generated, nothing is paid twice):

```
ARK_ASSET_BASE_URL=<forwarding-url> $PY -m runner.cli run --modality video --scenarios scenarios/bank-video-gaming --models configs/models-gaming-pilot.yaml --budget 220.00 --run 2026-09-17_121726_video
```

Resumed 17 September at about 13:55 through the temporary tunnel (hostname not recorded here):
13 cells (12 Seedance edits and a retry of the refused dugout cell), pre-flight $130.18 on top
of $77.56 already spent. The run's telemetry records the tunnel URLs, which stop resolving once
the tunnel is closed; the clips themselves stay in `video/assets/bank/gaming/video/`.

**Second failure on the resume.** With the tunnel serving correctly (200, video/mp4), all 13
resumed cells failed in under a second with `AccountOverdueError` (403) from BytePlus: the
Seedance account has an overdue balance after the $50.18 billed earlier in the day. Settle the
account, then resume again with the same command; the tunnel and file server must be up.

**Outcome.** 49 of 50 cells generated, 49 scored. Gemini Omni Flash 25/25, mean 8.39, $27.38.
Seedance 2.5 24/25, mean 8.34, $82.65 (image-to-video $50.18, edits $32.47). Judge $0.08.
Run total $110.12 of 220. One Seedance edit (VID-GEDIT-10, a 20 MB clip) timed out three
times at the judge's 300 s limit and was rated on a fourth attempt with the limit raised to
900 s (`judge --retry-unjudged` through a wrapper that sets `runner.judge.JUDGE_TIMEOUT_S`);
the dugout still was refused. Results workbook
`docs/sheets/gaming-video-run-2026-09-17.xlsx`, per-cell CSV beside it.

**Scorer gap on resume (gap log 17).** Cells that failed on the first pass keep a `failed` row
in `scores.jsonl`, and `score` skips any cell with a non-placeholder row, so cells that succeed
on a resume are never scored. Fix used: back up `scores.jsonl`, delete the stale `failed` rows
for those cells, run `score` again. The runner should treat such rows as placeholders.

**Seedance refusal.** The dugout still (VID-GI2V-06) was refused by Seedance's input filter as
"sensitive content: privacy information", i.e. a photoreal group of faces. Omni Flash accepted
the same still. Recorded as a result at no cost. A long run
must be launched detached from any tool or terminal that can time out, for example
`(nohup bash -c "<command> > run.log 2>&1" &)`; macOS has no `setsid`. A first start that
was tied to a tool shell was stopped after the pre-flight, before any cell was billed
(folder `2026-09-17_121607_video`, empty).

## Image lane, in order

Same four steps with `--modality image`, `scenarios/bank-image-gaming`,
`configs/models-gaming-pilot.yaml`, budget 2.00 (pre-flight $1.13 for 21 scenarios × 2
arms). Caution: the OpenAI credit is about $0.50; the GPT arm may fail partway unless topped
up. Failures are recorded as results, not missing rows. Image runs are deferred until Sai
says go.

## After the runs (pilot step 12)

Tick the run results checklist in the pilot doc: every Final scenario has an output from
every enabled arm or a recorded reason; first frame of each image-to-video output matches
its input still; "Must stay" items unchanged on edits; judge scored every cell; report shows
all three games and both arms; no file name or label reveals the model; cost within budget;
failures listed with scenario, model and reason.
