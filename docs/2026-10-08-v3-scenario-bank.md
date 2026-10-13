# v3 scenario bank — image and video — 8 October 2026

On 6 October 2026 Sahil (Google) reviewed the study. His view: our banks are too small and too
generic. The customers worth having already test at the 97th–98th percentile and run their own
evals; the value is in the hard corner cases, judged by experts. v3 is the bank written to the
98th–99th percentile: **300 image scenarios and 240 video scenarios**.

Scope is scenario writing only. No runner, adapter, judge or config changes, and nothing is run.

Sources: `image/v3-workbook/` and `video/v3-workbook/` (YAML plus a validating build script).
Built workbooks: `image/docs/sheets/2026-10-08-image-v3-scenarios.xlsx` and the video one.

## Sahil's six tests

From his screen-share of a customer's "NB pro tests" page
(`Pictures\Screenshots\Screenshot (223–244).png`). Each is recreated as a seed scenario.

| # | Test | Screenshots | What it showed | Seed |
|---|---|---|---|---|
| 1 | Composition and texture against a composition-led tool | 223–231 | Five prompt pairs (rider at sunset, club dancer, skincare still life, parent and child by the sea, longboarder on a mountain road). Sahil: "Gemini tends to center-frame and not play around with composition much." | `CMP-FLM-01` |
| 2 | Production bible from one product photo | 232–233 | One photo of a pineapple soda can becomes a dense sheet: hero product views, set design, storyboard, lighting and mood, style notes. | `SHT-ECM-01` |
| 3 | Storyboard from a base image | 234–235 | A base frame of an astronaut in a dark forest becomes a 3×3 pencil board, each panel captioned "action • shot size • camera". "This storyboard isn't bad, but the other has more detailing." | `STB-FLM-01` |
| 4 | Time-coded beat sheet | 236–237 | "THE LAST TRANSMISSION — SEGMENT: DESCENT & FAREWELL": nine beats over 0:00–0:45, start and end frames, camera, sound, voice-over, character, props, overlays, music plan, tech specs. | `SHT-FLM-01` |
| 5 | Typography posters against a type-led tool | 238–239 | An event poster with a large display word, a date, names and a Devanagari line; a collage poster with a long stacked headline and small body text. | `TYP-MED-01` |
| 6 | Many camera angles from one shot | 239–244 | A base image of two children under a tree, then wide establishment, two-shot, close-up left-angled, close-up right-angle, each its own prompt; and an "opposite side" reverse on a man at a crosswalk. | `COV-FLM-01` (and slot `COV-FLM-02`) |

Things Sahil named in the call that the families answer: storyboards for one character carried
into video; five shots of one room with consistency, for a camera move across it; spatial depth;
centred framing; texture against Midjourney; text against Recraft.

## What counts as one scenario

One brief and one answer key, judged as a whole. A six-angle coverage set is one scenario with
six outputs, because consistency can only be judged across the set. The workbook reports both
scenarios and outputs, so cost stays visible. Each set has a "must stay the same" list and each
member has its own checks.

## Rubric

Both banks are scored with one rubric (`image/v3-workbook/src/rubric.yaml`). The answer key of
every scenario is 12–25 yes/no checks (plus 2–5 per set member), and every check is filed under
one rubric criterion:

| Criterion | What it judges | Lanes |
|---|---|---|
| Prompt adherence | Everything the brief asks for: subjects, changes, counts, positions, camera, light, timing | both |
| Reference fidelity | A face, character, product or place from an input is the same one | both |
| Preservation | Everything the brief does not ask to change stays as it was | both |
| Set consistency | The outputs of a set, or the panels of one sheet, agree | both |
| Temporal consistency | Within one clip, things stay themselves from first frame to last | video |
| Motion coherence | Movement of people, objects and camera is plausible and continuous | video |
| Physics plausibility | Reflections, shadows, refraction, occlusion, contact, depth | both |
| Text accuracy | Required text exact in its script; no stray text | both |
| Composition | Framing, placement, scale and negative space as described | both |
| Style | The requested visual register | both |
| Visual quality | Clean anatomy and structure, no artefacts | both |

The rubric file also says what full and half marks look like for each criterion. A criterion
scores 10 × the share of its checks answered yes; a scenario scores the weighted mean of the
criteria it checks. Each family sets its own weights (coverage leans on set consistency, editing
on preservation, typography on text accuracy) and a gate: the criteria where any miss fails the
scenario. A scenario passes when it clears its gate and scores 8.0 or more.

How this lines up with the runner's judge configs (`image/configs/rubrics/`,
`video/configs/rubrics/`), which this work does not change:

- Same criterion, same key: prompt adherence, preservation, text accuracy (image), composition
  (image), physics plausibility (video), reference fidelity (video), motion coherence, temporal
  consistency, visual quality (image).
- Same criterion, different key in the configs: prompt adherence is `edit_fidelity` in the edit
  configs, style is `realism_style`, and video's visual quality is `visual_fidelity`.
- Not in the configs yet: set consistency in both lanes; reference fidelity and physics
  plausibility for image; text accuracy, composition and style for video.
- Measured by code in the configs, not judged: technical compliance (size, duration, frame rate)
  in video, and image-edit preservation (pixel distance to the source). The bank's preservation
  checks are judged questions; the measurement can sit beside them.

## Industries

| Industry | Share | Image | Video |
|---|---|---|---|
| Film, OTT & micro-drama | 25% | 75 | 60 |
| Advertising & agencies | 20% | 60 | 48 |
| E-commerce, D2C & retail (incl. fashion, beauty, food) | 20% | 60 | 48 |
| Gaming | 15% | 45 | 36 |
| Real estate, interiors & architecture | 10% | 30 | 24 |
| Media, publishing & events | 10% | 30 | 24 |

## Image families (300, about 630 output images per model)

| # | Code | Family | Qty | t2i | edit | Film | Ads | Ecom | Gaming | RE | Media | Outputs each |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | COV | Multi-angle coverage | 45 | 10 | 35 | 15 | 6 | 6 | 6 | 10 | 2 | 5–8 (set) |
| 2 | STB | Storyboards | 30 | 10 | 20 | 14 | 8 | 2 | 6 | – | – | 1 grid |
| 3 | SHT | Production sheets | 25 | 10 | 15 | 8 | 7 | 5 | 5 | – | – | 1 sheet |
| 4 | CHR | Character continuity | 35 | 10 | 25 | 16 | 4 | 5 | 10 | – | – | 3–6 (set) |
| 5 | CMP | Composition and aesthetic | 35 | 30 | 5 | 10 | 7 | 6 | 3 | 3 | 6 | 1 |
| 6 | TYP | Typography and layout | 40 | 30 | 10 | 4 | 8 | 8 | 4 | – | 16 | 1 |
| 7 | PRD | Product and brand | 35 | 5 | 30 | – | 10 | 20 | 3 | 2 | – | 1 |
| 8 | EDT | Precision editing | 25 | 0 | 25 | 3 | 4 | 8 | 4 | 4 | 2 | 1 |
| 9 | PHY | Depth, light and physics | 30 | 20 | 10 | 5 | 6 | – | 4 | 11 | 4 | 1 |
| | | **Total** | **300** | **125** | **175** | 75 | 60 | 60 | 45 | 30 | 30 | |

## Video families (240, about 410 clips per model)

| # | Code | Family | Qty | Built on | t2v | i2v | edit | Clips each |
|---|---|---|---|---|---|---|---|---|
| V1 | VCOV | Coverage clips and a camera move through a space | 40 | COV | 10 | 30 | 0 | 4 or 1 move |
| V2 | VSTB | Storyboard panel to shot | 25 | STB | 0 | 25 | 0 | 1 |
| V3 | VBTS | Story beats (first and last frame, consecutive beats) | 25 | SHT | 0 | 25 | 0 | 3 |
| V4 | VCHR | Character continuity and performance (no lip-sync) | 30 | CHR | 7 | 18 | 5 | 3 |
| V5 | VSPT | Product and ad spots | 30 | PRD | 5 | 20 | 5 | 1 multi-shot |
| V6 | VEDT | Precision edits on real footage | 35 | EDT | 0 | 0 | 35 | 1 |
| V7 | VPHY | Physics, parallax and depth | 30 | PHY | 20 | 5 | 5 | 1 |
| V8 | VCAM | Cinematography and composition in motion | 15 | CMP | 8 | 4 | 3 | 1 |
| V9 | VTXT | Text and graphics in motion | 10 | TYP | 5 | 0 | 5 | 1 |
| | | **Total** | **240** | | **55** | **127** | **58** | |

Video by industry: VCOV 12/5/5/6/9/3, VSTB 11/6/2/6, VBTS 8/7/5/5, VCHR 12/4/5/9, VSPT –/12/14/2/2,
VEDT 7/6/9/4/4/5, VPHY 5/5/3/3/7/7, VCAM 4/2/3/1/2/3, VTXT 1/1/2/–/–/6 (Film, Ads, Ecom, Gaming, RE,
Media), giving 60/48/48/36/24/24. The task split per industry is in `video/v3-workbook/build_workbook.py`.
Video adds four levers that only exist in motion — `camera-move`, `timing`, `end-frame`, `parallax` —
and two rubric criteria, motion coherence and temporal consistency.

Video stops at 240: clips cost about $1–4 each per model, and the extra 60 would mostly be more
single shots. Minimum shares the build enforces: image t2i 35%, edit 50%; video t2v 20%, i2v 45%,
edit 20%.

## Hardness levers

Every scenario uses at least three; every lever appears in at least three families, except
`set`, which only the two set families (coverage and character) can use.

| Lever | What makes it hard |
|---|---|
| set | Several outputs that must agree with each other |
| unseen | Has to show what the input never shows (reverse side, back, behind the camera) |
| count | Exact numbers: people, objects, panels, beats |
| layout | Exact positions: who is left or right, what is where |
| exact-text | Exact words that must appear letter for letter |
| script | Text in Devanagari, Telugu or Tamil |
| small-text | Small or dense text that must stay readable |
| exclude | Named things that must not appear |
| off-centre | Composition against the default centred framing |
| camera | A named lens, shot size, camera height or angle |
| light-lock | A light direction or time of day that must hold |
| physics | Reflections, shadows, refraction, occlusion or contact |
| identity | A face, character or product kept from a reference |
| multi-ref | Two to four inputs combined |
| locale | Indian cultural detail that a default model gets wrong |
| keep-rest | Everything outside the change stays the same |
| multi-step | Several changes in one instruction |
| long-brief | A long brief with ten or more constraints |
| continuity | Screen direction, props or costume carried across frames |
| time-code | Time codes or durations that must add up |

Difficulty per family is about 60% hard and 40% extreme. Hard: a strong model gets most of it
and misses a detail an expert would catch. Extreme: several levers stacked; even the best output
is expected to miss something.

## Diversity

Every scenario is tagged on seven axes (family, industry, subject, style, light, locale and
script, task). The tags live on the slot, so diversity is designed before writing. The build
enforces: every family covers 4+ industries, 6+ subjects, 3+ styles and 4+ light conditions; no
subject above 25% of a family; 30%+ Indian context; text families use Latin, Devanagari, Telugu
and Tamil; no two scenarios share family, subject, industry and failure target.

## Sources

Every input has a model-neutral source prompt (about 150 across both lanes: ~70 base scenes,
~40 product, logo and character references, ~40 source clips). Base scenes are reused by 2–5
scenarios across families and into video (cap 5), so the image-to-video chain is built in. The
build rejects any source prompt that names a model or vendor or uses tool syntax. Never use a
model in the comparison to make its own inputs; provenance is recorded when a source is made.

## Seeds (27 image, 27 video)

Three per family, spread across industries, including the six recreations above.

| Family | Seeds (image) |
|---|---|
| COV | Film (Sahil's angles), Real estate, Gaming |
| STB | Film (Sahil's storyboard), Ads, Gaming |
| SHT | Film (Sahil's beat sheet), E-commerce (Sahil's product bible), Ads |
| CHR | Film, Gaming, E-commerce |
| CMP | Film (Sahil's composition), Media, Real estate |
| TYP | Media (Sahil's typography), Ads, E-commerce |
| PRD | E-commerce, Ads, Gaming |
| EDT | Film, E-commerce, Ads |
| PHY | Film, Real estate, Media |

## Order of work

Image first, then video, one industry at a time, each reviewed before the next:

1. Film, OTT & micro-drama — 75 slots, 20 sources, 7 seeds (`COV`, `STB`, `SHT`, `CHR`, `CMP`,
   `EDT`, `PHY`). Done.
2. Advertising & agencies — 60 slots, 14 sources, 5 seeds (`STB`, `SHT`, `TYP`, `PRD`, `EDT`).
   Done.
3. E-commerce, D2C & retail — 60 slots, 18 sources, 5 seeds (`SHT` with Sahil's product bible,
   `CHR`, `TYP`, `PRD`, `EDT`). Done.
4. Gaming — 45 slots, 10 sources, 4 seeds (`COV`, `STB`, `CHR`, `PRD`). Done.
5. Real estate, interiors & architecture — 30 slots, 10 sources, 3 seeds (`COV`, `CMP`, `PHY`).
   Done.
6. Media, publishing & events — 30 slots, 6 sources, 3 seeds (`CMP`, `TYP` with Sahil's
   typography, `PHY`). Image slot grid complete (300 slots, 78 sources, 27 seeds).
7. Image scenarios: all 300 written (558 outputs per model; 228 runnable today, 72 partly,
   all of them sets that need set review). Every build rule passes. **Done, awaiting review.**
   The seed review with Gaurav and Sahil can use the full bank.
8. Video foundation: build script, nine family headers with failure targets, empty sources
   file, per-industry plan. Done.
9. Video, one industry at a time in the same order, each approved before it starts.
   - Film, OTT & micro-drama — 60 slots, 20 new sources (8 source clips, 9 storyboard pages, 3
     stills), 7 seeds (`VCOV`, `VSTB`, `VBTS`, `VCHR`, `VEDT`, `VPHY`, `VCAM`). Done.
   - Advertising & agencies — 48 slots, 15 new sources (6 clips, 5 storyboard pages, 4
     references), 5 seeds (`VSTB`, `VBTS`, `VTXT`, `VSPT`, `VEDT`). Done.
   - E-commerce, D2C & retail — 48 slots, 12 new sources (9 clips, 2 storyboard pages, the Ananas
     production bible), 5 seeds (`VSPT` with Sahil's product bible, `VBTS`, `VCHR`, `VTXT`, `VEDT`).
     Done.
   - Gaming — 36 slots, 12 new sources (4 in-game clips, 5 storyboard pages, 3 character sheets),
     4 seeds (`VCOV`, `VSTB`, `VCHR`, `VSPT`). Done.
   - Real estate, interiors & architecture — 24 slots, 4 new source clips, 3 seeds (`VCOV`, `VPHY`,
     `VCAM`). Done.
   - Media, publishing & events — 24 slots, 6 new source clips, 3 seeds (`VTXT` with Sahil's
     typography, `VPHY`, `VCAM`). Video slot grid complete (240 slots, 69 video sources plus 78 image
     sources reused, 27 seeds). Done.
10. Video scenarios: all 240 written (368 clips per model; 179 runnable today, 61 partly, all of
    them sets that need the set review). Every build rule passes, including every lever
    in at least 3 families. **Done, awaiting review.**
