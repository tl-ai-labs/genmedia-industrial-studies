# Video scenario bank v3 (workbook source)

240 hard video scenarios across 9 families and 6 industries, written to the same bar as the image
bank (`image/v3-workbook/`) and built on it: every video family extends an image family, and
image-to-video scenarios start from the image bank's sources. Background, Sahil's six examples,
the families and the hardness levers: `docs/2026-10-08-v3-scenario-bank.md`.

    voice/.venv/bin/python video/v3-workbook/build_workbook.py            # validate and build
    voice/.venv/bin/python video/v3-workbook/build_workbook.py --check    # validate only

The `.xlsx` (`video/docs/sheets/2026-10-08-video-v3-scenarios.xlsx`) is built output; edit
`src/*.yaml` and rebuild. The build stops on any rule that is already broken. Nothing here runs
a model.

## Progress

All 240 scenarios written: 368 clips per model, 179 runnable today and 61 partly (all of them sets,
which need the set review; chained sets also need chaining in the runner). Every build rule passes.

| Industry | Slots | Scenarios written |
|---|---|---|
| Film, OTT & micro-drama | 60 of 60 | 60 (7 seeds) |
| Advertising & agencies | 48 of 48 | 48 (5 seeds) |
| E-commerce, D2C & retail | 48 of 48 | 48 (5 seeds) |
| Gaming | 36 of 36 | 36 (4 seeds) |
| Real estate, interiors & architecture | 24 of 24 | 24 (3 seeds) |
| Media, publishing & events | 24 of 24 | 24 (3 seeds) |

## Families and plan

| Code | Family | Built on | Qty | t2v | i2v | edit | Clips each | Film | Ads | Ecom | Gaming | RE | Media |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| VCOV | Coverage and camera move | COV | 40 | 10 | 30 | 0 | 4 (set) or 1 move | 12 | 5 | 5 | 6 | 9 | 3 |
| VSTB | Storyboard to shot | STB | 25 | 0 | 25 | 0 | 1 | 11 | 6 | 2 | 6 | – | – |
| VBTS | Story beats | SHT | 25 | 0 | 25 | 0 | 3 (set) | 8 | 7 | 5 | 5 | – | – |
| VCHR | Character continuity in motion | CHR | 30 | 7 | 18 | 5 | 3 (set) | 12 | 4 | 5 | 9 | – | – |
| VSPT | Product and ad spots | PRD | 30 | 5 | 20 | 5 | 1 multi-shot | – | 12 | 14 | 2 | 2 | – |
| VEDT | Precision video edits | EDT | 35 | 0 | 0 | 35 | 1 | 7 | 6 | 9 | 4 | 4 | 5 |
| VPHY | Physics and depth in motion | PHY | 30 | 20 | 5 | 5 | 1 | 5 | 5 | 3 | 3 | 7 | 7 |
| VCAM | Camera work | CMP | 15 | 8 | 4 | 3 | 1 | 4 | 2 | 3 | 1 | 2 | 3 |
| VTXT | Text in motion | TYP | 10 | 5 | 0 | 5 | 1 | 1 | 1 | 2 | – | – | 6 |
| | **Total** | | **240** | **55** | **127** | **58** | | 60 | 48 | 48 | 36 | 24 | 24 |

The per-industry task split is in `PLAN` at the top of `build_workbook.py`; extreme counts per
family (about 40%) are in `EXTREME`.

## Files

- `src/customers.yaml` — the 1–3 target-customer types each scenario is for, drafted from its
  persona (2026-10-08, to be reviewed); the types are the image bank's `src/customers.yaml`.
- `src/sources.yaml` — video-only inputs: source clips for edits, and new images the image bank
  does not have. Image sources are reused by id, not copied.
- `src/<fam>.yaml` — one header per family (`vcov`, `vstb`, `vbts`, `vchr`, `vspt`, `vedt`, `vphy`,
  `vcam`, `vtxt`): name, rubric weights and gate, failure targets, set size and the slot grid.
- The rubric is the image bank's `src/rubric.yaml`, shared by both banks; video uses the criteria
  marked for video.
- `src/<fam>-<ind>.yaml` — part files holding only `code` and `scenarios`.

## Slots

    - {id: VCOV-FLM-01, task: i2v, subject: child-elderly, style: cinematic, light: golden,
       locale: IN, diff: extreme, target: move-wrong, sources: [SRC-KIDS-TREE],
       from_image: COV-FLM-01, idea: "..."}

`task` is `t2v`, `i2v` or `edit`. `from_image` (optional) names the image scenario the slot
extends; it must belong to the family this one is built on.

## Scenarios

The image scenario shape, plus:

    clip: {duration: 8, aspect: "16:9", camera: "slow push in from 6 m to 2 m"}   # one clip's seconds;
                                                       #   a chained set: the whole passage
    inputs:
      - {source: SRC-KIDS-TREE, role: first_frame}     # i2v roles: first_frame, last_frame,
      - {source: SRC-...,       role: last_frame}      #   character, environment, product, logo,
                                                       #   style, still1–still4
      - {source: SRC-CLIP-...,  role: source}          # edit: exactly one source clip

Sets (coverage, story beats, character) are one scenario with 3–5 clips; story-beat sets are
usually `chained` (each clip starts from the last frame of the one before). No lip-sync or audio
is judged.

## Rubric

Both banks are scored with one rubric (`image/v3-workbook/src/rubric.yaml`), and the scoring,
weights, gates and pass score work the same way (see the image README). Video adds two criteria
that only exist in motion:

- **Motion coherence:** movement of people, objects and camera is plausible and continuous: weight,
  momentum, contact, a camera path that holds, no warping or sliding.
- **Temporal consistency:** within a clip, things stay themselves from first frame to last: no
  morphing, flicker, swimming textures or re-spelled text. Agreement between the clips of a set
  is set consistency.

Every video scenario checks at least one of the two.

## Build rules

Everything in the image bank's rules applies (slot fields, plan per industry, near-duplicates,
subject cap, extreme count, model-neutral prompts, 12–25 checks, exact text in the prompt and in
the slot's script, set rules, coverage rules per family and for the bank). In addition:

- **Tasks and inputs:** t2v has no inputs; i2v names one or more image sources and no clips, with
  at most one `first_frame` and one `last_frame` (and no `last_frame` without a `first_frame`);
  an edit names exactly one source clip, with role `source`.
- **Sources across lanes:** a video source may not redefine an image source; a scene's image and
  video uses together are capped at 5.
- **Clips:** a source clip states `duration` (3–20 s), `camera_move` and `action_timing`; a
  scenario's `clip` states its duration, aspect and camera.
- **Minimum shares** for the full bank: t2v 20%, i2v 45%, edit 20%.
- **Levers:** the image levers plus `camera-move`, `timing`, `end-frame` and `parallax`; every
  lever in at least 3 families, except `set`.
- **Rubric:** the image bank's rubric rules, plus at least one motion coherence or temporal
  consistency check per scenario.
