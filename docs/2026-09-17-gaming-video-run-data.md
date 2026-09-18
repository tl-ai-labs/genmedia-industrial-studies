# Gaming video run: where the data is and how it reaches the dashboard

Run `2026-09-17_121726_video`, 25 gaming scenarios (BGMI, Real Cricket 24, GTA V),
Gemini Omni Flash vs Seedance 2.5, judged blind by Gemini 3 Flash. Generated 17
September 2026 on this branch (`docs/gaming-scenario-plan`). Runs are immutable: nothing
below is edited by hand after the run, except the one scorer fix recorded in gap log 17.

## The record, in this repo

| What | Where | Notes |
|---|---|---|
| Run folder | `video/runs/2026-09-17_121726_video/` | `manifest.json`, `telemetry.jsonl`, `judge.jsonl`, `scores.jsonl`, `checks.jsonl`, frozen `scenarios/` and `inputs/`, `outputs/video/<id>/<model>.mp4`, `previews/`, `report.html`, `report-client.html` |
| Scenario files that were run | `video/scenarios/bank-video-gaming/*.yaml` | 13 image_to_video, 12 video_edit; the run folder holds frozen copies |
| Inputs | `video/assets/bank/gaming/video/` | 13 stills, 12 six-second clips, all generated placeholders; provenance per file in the sheet's Generation details column |
| Scenario bank sheet | `docs/sheets/gaming-scenarios-video.xlsx` | Scenario bank layout plus the gaming columns, briefs, gap log 1–21 |
| Results workbook | `docs/sheets/gaming-video-run-2026-09-17.xlsx`, `-cells.csv` | per scenario, per cell, cost, run facts |
| Observations | `docs/2026-09-17-gaming-video-observations.md` | plain-language, same format as `video/runs/merged-video-all/OBSERVATIONS-simple.md` |
| Pilot doc, runbook | `docs/2026-09-15-gaming-pilot-bgmi-cricket.md`, `docs/2026-09-17-pilot-run-commands.md` | step status, checklist, commands, gaps |
| Industry mapping | `video/configs/industry_map.yaml` | the 25 gaming scenarios added as `primary: Gaming` on 17 Sep so the report's industry rollup places them |

Headline figures (runner-measured): Gemini 25 of 25 clips, mean rating 83.9%, $27.38;
Seedance 24 of 25 generated, 24 rated, mean 83.4%, $82.65; judge $0.08; 24 scenarios
compared, Gemini 12 wins, Seedance 12, no ties. (The Seedance clip on VID-GEDIT-10 was
rated on a fourth judge attempt with a 900 s timeout, 17 September 16:20; every figure
here and in the workbook includes it.)

## How it reaches studies.adlc.tilicho.in

The dashboard in `tl-ai-labs/ai-sdlc-multi-model-orchestration` does not read run folders.
Its script `tools/genmedia-extract-evidence.mjs` reads **one client report per lane** from
this repo, copies every figure and label into `dashboard/public/data/studies/genmedia-model-comparison/evidence/video.json`
and writes the embedded previews out to `media/video/<id>/`. The headline numbers, cost
and run facts on the card live in `dashboard/public/data/studies.json` and are
transcribed by hand in the same commit.

The video lane is one setting, so the gaming run is folded into the existing 18-scenario
video report rather than shown beside it:

```
cd video
python -m runner.cli merge-report \
  --run 2026-09-10_145714_video --run 2026-09-10_170453_video --run 2026-09-11_094614_video \
  --run 2026-09-11_103922_video --run 2026-09-11_105822_video --run 2026-09-11_112029_video \
  --run 2026-09-11_113427_video --run 2026-09-17_121726_video \
  --out video/runs/merged-video-all-2026-09-17 \
  --self-contained --complete-only --group-task text_to_video=image_to_video --preview-crf 34
```

One trap: the seven earlier run folders as committed in this repo are missing clips that
exist only in the main checkout, uncommitted (for example the 11 September re-cuts of
VID-EDIT-03 to 09). A merge built from a fresh checkout silently drops that media. Copy
those run folders in first (`rsync -a --ignore-existing` from the checkout that produced
`merged-video-all`), or build the merge from that checkout. Gap log 19.

Same flags as the published `merged-video-all` report, plus the gaming run: 43 scenarios,
two industries (Ecommerce & Retail, Gaming) and three use-case families. Then, in the
dashboard repo, point `REPORTS.video.file` in the extractor at the new report and run:

```
node tools/genmedia-extract-evidence.mjs --src /path/to/genmedia-industrial-studies
```

and update `studies.json` (video lane: scenario count, cost rows for the new run, the
`sources.report` sha256 and size, `sources.runs`, and the card's headline sentences).

## Status on 17 September

Done locally, not yet committed or pushed to the dashboard repo. Everything below was
rebuilt at 16:30 after the fourth judge attempt rated the Seedance clip on VID-GEDIT-10
(scratchpad `post-judge.sh run | merge | publish` chains the steps; `extract-report-data.js`
now carries cost, gates, resolution and criterion scores itself):

- `video/runs/merged-video-all-2026-09-17/` built from the eight runs: 43 scenarios, 95
  previews, `report-client.html` 113.3 MB, sha256 `607113938e5a84930a47a4e5b791ae5f91832be056bf99615fb8bc80f3ea9941`.
  The merged manifest carries the first run's commit (`a92dc5562c`) and scenario-set hash
  (`c1cde1913837`), as the runner's merge does.
- Extractor run: `evidence/video.json` now holds 43 scenarios (75 arm clips, 362 criterion
  rows, industry and family rollups); `media/video/` has 43 folders, 85 MB (was 46 files);
  the published 18 scenarios keep every clip, rating and winner they had.
- `studies.json` video lane: 43 scenarios; card figures copied from the report's
  image-to-video summary (quality 86.6% vs 91.3%, reliability 57.5% vs 79.5%, failed 0 vs 3
  of 23, cost per clip $1.271 vs $3.818, latency p50 72.5 s vs 223.1 s, tally 8-0-12 over
  20 decided scenarios); cost row summed from the merged run's own telemetry and judge
  records (Omni $52.157 over 45 attempts, Seedance $169.801 over 39, judge $0.149 over 80
  calls); study description now says 200 scenarios.
- Both dashboard tests pass (33 of 33) with this repo checked out beside the clone.

Report-wide figures for the Slack note: 43 scenarios, 35 compared, Omni Flash 16 wins,
2 ties, Seedance 17 wins. Video edits (rating over the decided scenarios): 86.5% vs 80.6%,
8-2-5 to Omni Flash.

The family rollup shows the gaming scenarios under one family, `gaming-pilot`, because the
report takes a scenario's family from its first tag and that is the first tag on every
gaming scenario file. A later run can carry `gaming-gameplay-moments` and
`gaming-clip-editing` as first tags if two families are wanted; the frozen files of this
run are not edited.
