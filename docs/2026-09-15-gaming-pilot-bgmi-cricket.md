# Gaming Scenario Pilot — BGMI and Real Cricket 24

Date: 15 September 2026. Requested by Ravi.

Follows the collection plan in `docs/2026-09-14-gaming-scenario-plan.md`.
Every step below points to the section of that plan it tests.

## Why we are doing a pilot

Before we write scenarios for all 18 games, we run the whole plan end to end
on **two games** and check that it works in practice. The pilot must show
that the process produces the expected scenarios, a correct sheet, and real
run results. Every step is checked against the plan. Every problem we hit is
written down so we can fix the plan before scaling.

## Scope

| | Pilot |
|---|---|
| Games | BGMI (battle royale, realistic) and Real Cricket 24 (cricket, TV style) |
| Lanes | Video (Image-to-Video, Video Editing) and Image (Text-to-Image, Image Editing) |
| Drafts | 7–8 scenarios per game per lane, about 30 in total |
| Final | 5–7 scenarios per game per lane, 20–28 in total, split roughly evenly between the two tasks in each lane |
| Team | Senior team members only. One Collector per lane, one Reviewer across both |
| Review gates | Ravi reviews the scenarios before any run, and reviews the run results at the end |
| Out of scope | The other 16 games. Any change to the runner beyond what a run needs |

Why these two games: they are the two most different games on the list that
we know well. BGMI gives open landscapes, vehicles and weapons in third
person. Real Cricket 24 gives ball physics, broadcast camera and crowds. If
the plan works for both, it will work for the games in between.

## Team

| Role | Who | Does |
|---|---|---|
| Collector, video lane | (senior, to be named by Ravi) | Game briefs, video scenario cards, sources, sheet rows |
| Collector, image lane | (senior, to be named by Ravi) | Image scenario cards, sources, sheet rows |
| Reviewer | (senior, to be named by Ravi) | Relevance check, duplicates, scoring, ranking, run validation, gap log |
| Approver | Ravi | Reviews scenarios before runs, approves budget, reviews run results, decides on scaling |

The two collectors share the game briefs. Whoever finishes a brief first
sends it to the other.

## Step-by-step with a check at every step

Each step is done exactly as the plan says. The Reviewer ticks the check
before the next step starts. If a check fails, we stop, fix, and write the
problem in the gap log at the bottom.

| # | Step | Plan section | Pass when | Evidence |
|---|---|---|---|---|
| 1 | Confirm the two games and the four test-type lists | 2, 4.1 | Ravi has confirmed the two games. Both collectors have read all four test-type lists | Message from Ravi. Collectors say so in the channel |
| 2 | Write the game brief for BGMI and for Real Cricket 24 from each game's official website | 3 | Both briefs have all headings filled, using the game's own names for modes, maps, weapons, shots, stadiums, and the Sources row lists the official URLs read and the dates | Two briefs on the Game briefs tab of each sheet |
| 3 | Find real prompts people have shared in public for each game, and write 7–8 scenario cards per game per lane from them | 4 | Every card has every column filled. Prompt origin is Real on every card, with source link, source name and source assets, or Team-written with the reviewer's approval noted. Every card names at least two things from the brief. No card fails the generic test. No two prompts pass the swap test | About 30 rows, Status = Draft |
| 4 | Get the input for every card that needs one, from the same public post, an official source or our own recording. Create one only if none exists | 5 | Every non-Text-to-Image row has a source link, source name, source assets, source type, usage note and a file that opens. Text-to-Image rows have a reference picture. Generated sources have prompt, model and date | Files in `assets/bank/gaming/video/` and `assets/bank/gaming/image/`. Status = Sourced |
| 5 | Relevance check of every row | 6 | Every row marked Relevant, Replaced or Regenerated. Every Replaced or Regenerated row has a note saying why | Relevance column filled |
| 6 | Remove duplicates and generic scenarios | 7 | No test type used more than three times in a lane. No two rows share test type and subject. Every dropped row has a reason in Status | Sorted sheet, Status notes |
| 7 | Score every card | 8 | Every kept card scores 4+ average, 4 or 5 on "about this game", nothing below 3 | Reviewer score column filled |
| 8 | Rank and pick the final list | 9 | 5–7 per game per lane. Both tasks present in each lane. Reserve list at the bottom | Status = Final on the kept rows |
| 9 | **Ravi review of the scenarios** | — | Ravi has read every Final row and either approved it or sent it back. Sent-back rows go through steps 3–8 again | Ravi's comments in the sheet or the channel |
| 10 | Write the scenario files | 10 | One file per Final row in `video/scenarios/bank-video-gaming/` and `image/scenarios/bank-image-gaming/`. The runner loads them without errors | Runner dry run passes |
| 11 | Run the scenarios | see below | Every Final scenario has an output from every enabled model, or a recorded reason why not | Run folders and the cost report |
| 12 | Validate the run results | see below | All items in the run checklist below ticked | Ticked checklist, judged report |
| 13 | **Ravi review of the results** | — | Ravi has seen the sheet, the report and the gap log | Message from Ravi |
| 14 | Gap review and decision to scale | — | Every gap has a fix or a decision. The plan is updated | Updated plan, updated gap log |

## Where the current drafts stand (after the 15 September search)

The 32 cards drafted on 15 September were searched against public sources
the same day (search log at the end of
`docs/2026-09-15-pilot-scenario-cards.md`). Six new cards were added from
real prompts found on the way. The sheets now hold 38 cards:

| Prompt origin | Video | Image | Meaning |
|---|---|---|---|
| Real | 4 | 10 | Posted prompt used word for word, only game or brand names replaced |
| Real-based | 3 | 5 | A real public prompt is the core, but more than brand names changed. Needs reviewer approval |
| Team-written | 10 | 6 | No public post with such a prompt and a result was found. Needs reviewer approval |

Every Real and Real-based card has Source link, Source name, Source
asset(s), Source prompt (verbatim) and Other source details filled. Every
Team-written card says where we searched.

What is still open before step 3 is done:

1. The reviewer decides on gap log item 1: whether "Real-based" is allowed
   as a third Prompt origin value, or those 8 cards must be re-sourced or
   re-labelled.
2. The reviewer approves or rejects each of the 16 Team-written cards. Most
   are Image-to-Video gameplay moments and technical video edits (slow
   motion, overlays, smoothing), for which no public prompts were found.
3. The collector copies the full text of the one partial quote
   (IMG-GT2I-07) by hand from the source page.
4. The game briefs were written from team knowledge. Step 2 means checking
   every line against the official website and store page, correcting what
   differs, and filling the Sources row with the URLs and dates.

## 17 September additions: GTA V and a second prompt search

- **GTA V joins the video lane** with 8 cards: 4 image-to-video from Starrd's
  GTA prompt library (real prompts, brand and city names replaced) and 4
  video edits taken word for word from Picsart's editing-prompt collection.
  Its game brief is built from Rockstar's own GTA V and GTA Online pages;
  lines the official pages do not cover are marked "observed in play".
- **Second search round** unblocked 5 of the 10 blocked video cards: the
  slow-motion, smoothing and blur edits (Picsart, LumeFlow, Kapwing), the
  speed-readout overlay (LumeFlow), and the wicket celebration, now a dugout
  celebration from a real cricket broadcast video prompt (praveenbhat.net).
  Gap log items 10 and 11.
- **Third search round** (same day, at Sai's request) found a real public
  prompt behind every card that was still blocked, 5 video and 6 image. None
  is a word-for-word match, so all 11 are **Real-based**: a real prompt about
  a different subject is the core and our game details are put in. Two video
  cards were reworked so the real prompt genuinely is the core: the jeep now
  climbs a wet rock before it stops (Kling off-road prompt on the UlazAI
  directory), and the run-out throw became a boundary catch (TechMitra T20
  video prompt). Their two stills must be regenerated. Gap log items 12 and
  13; sources in the sheet and in the review copy.
- Lanes now: video 25 cards, all 25 in the run set (10 Real, 15 Real-based),
  0 blocked; image 21 cards, all 21 in the run set (10 Real, 11 Real-based),
  0 blocked. Eleven inputs to generate (7 stills, 4 clips).

| Re-sourced card | Was | Now (Real-based, needs reviewer approval) | Source |
|---|---|---|---|
| VID-GI2V-01 jeep sequence | Team-written | Jeep climbs a wet rock, stops, two players out, smoke, revive | UlazAI directory, Iqra Saifi, Kling, 22 Aug 2025 |
| VID-GI2V-02 airdrop | Team-written | Crate lands with a thud, red smoke spreads, sniper crawls | UlazAI directory, moonproductions.services, Veo 3 Fast, 1 Sep 2025 |
| VID-GI2V-04 mass jump | Team-written | Twenty players freefall from the plane, parachutes open, coastline below | UlazAI directory, Iqra Saifi ("Skydiving Cows"), 16 Aug 2025 |
| VID-GI2V-05 run-out | Team-written | Boundary catch: ball grows larger, hands come up, tumble | TechMitra, Ayush Singhal, 13 Feb 2026 |
| VID-GI2V-07 HUD-only | Team-written | "Move only the broadcast graphics. Nothing else in the frame changes" | Prompt Architects, Nafiul Hasan, 27 Aug 2026 |
| IMG-GT2I-02 aerial town | Team-written | Aerial photography of countryside, farmhouses and a small hillside town | Imagine with Rashid, Mohammed Rashid, 23 Jun 2024 |
| IMG-GT2I-03 first-person rifle | Team-written | "screenshot of a first person shooter game on unreal engine 5" with our rifle and bridge | PromptHero, Stable Diffusion 1.4, 13 Aug 2022 |
| IMG-GT2I-04 full HUD | Team-written | "In-game screenshot ... authentic-looking game UI" with our six elements | Morphic, ChatGPT Images 2.0 prompt library |
| IMG-GEDIT-04 add minimap | Team-written | "Overlay an invented ... game HUD element" reduced to one minimap | Starrd, Brian Bautista, 24 Jun 2026 |
| IMG-GT2I-08 field-setting diagram | Team-written | Clean formation graphic with eleven labelled dots on a cricket field | ImagineArt, Tooba Siddiqui, 11 Jun 2026 |
| IMG-GEDIT-06 score-bar text | Team-written | "Change the text 'X' to 'Y' on the score bar, keeping the same style" | Wiro AI, wiromlteam, 2 Sep 2025 |

## Step 4 status, 17 September

All 33 model inputs exist, generated on Vertex project temp-genmedia-study
after ai-studies-console was suspended for Vertex AI (gap log item 9). The
second wave on 17 September (Sai's go) produced 7 stills ($0.47) and 4 GTA V
clips ($9.60 billed). Three of the stills came back with real names on them
and were regenerated with fictional names forced ($0.20); the four GTA V
clips came back with a mobile touch joystick and were regenerated in a
console-game style ($9.60 billed, gap log item 14). Every input was viewed
before use.

| Set | Files | Model | Cost | Where |
|---|---|---|---|---|
| 21 stills (13 image-to-video, 8 image edits) | png, 1264x848 | Nano Banana Flash | $1.61 plus $0.27 for four regenerations | `video/assets/bank/gaming/video/`, `image/assets/bank/gaming/image/` |
| 12 source clips (video edits) | mp4, 1920x1080, 24 fps, 6 s | Veo 3.1 | $28.80 plus $9.60 for the GTA V regeneration | `video/assets/bank/gaming/video/` |

Every generated file is a placeholder until an own screenshot replaces it;
the sheet's Generation details column names the model, run and scenario for
each. One still (VID-GI2V-09, fan in the stands) was regenerated with an
explicit cricket setting because the first output read as a football
stadium. The 13 Text-to-Image judge references still need real screenshots.

Evaluation dry-runs pass: video 25 scenarios × 2 arms, pre-flight $216.60
(about $194 likely: Omni Flash about $31, Seedance about $163); image 21
scenarios × 2 arms, pre-flight $1.13. The video run was started on 17
September with a budget of 220 on Sai's approval; the image run is deferred.
Commands are in `docs/2026-09-17-pilot-run-commands.md`.

## Step 11 and 12 status, 17 September (video lane)

Run `2026-09-17_121726_video`, budget 220, both arms, all 25 scenarios. Results
workbook: `docs/sheets/gaming-video-run-2026-09-17.xlsx` (Scenarios, Cells, Cost,
Run tabs); per-cell CSV beside it; full report in the run folder (211 MB).

| Arm | Cells | Generated | Judged | Mean score (0–10) | Avg latency | Billed |
|---|---|---|---|---|---|---|
| Gemini Omni Flash, image-to-video | 13 | 13 | 13 | 8.39 | 77 s | $15.84 |
| Gemini Omni Flash, video edits | 12 | 12 | 12 | 8.40 | 104 s | $11.54 |
| Seedance 2.5, image-to-video | 13 | 12 | 12 | 8.96 | 206 s | $50.18 |
| Seedance 2.5, video edits | 12 | 12 | 12 | 7.73 | 336 s | $32.47 |
| Judge (Gemini 3 Flash, blind A/B) | 50 | | 49 | | | $0.08 |
| **Run total** | | 49 of 50 | 49 | | | **$110.12** of 220 |

Per model: **Gemini Omni Flash $27.38** for 25 cells (mean 8.39); **Seedance
2.5 $82.65** for 24 cells (mean 8.34). The Seedance edits came to about $2.71
a cell, far below the $10.50 cap the pre-flight assumed; the runner marks
some Seedance rows as estimates until the BytePlus invoice confirms them.

How the run went: the 12 Seedance edits failed twice at no cost, first because
the source clips were not fetchable by the provider (gap log 15), then because
the Seedance account was overdue; they completed in a resume at 14:05–14:31
after Sai served the clips from the Mac through a temporary tunnel and settled
the account. Seedance's input filter refused the dugout still (gap log 16). The
judge's model call timed out three times on one Seedance edit output (VID-GEDIT-10)
at the runner's 300 s limit; a fourth attempt with the limit raised to 900 s rated it
(82.5%) at 16:20 (gap log 21). The scorer needed a manual fix to score cells that had
failed on the first pass (gap log 17).

Head to head, using the team's rule that any higher rating is a win and only
identical ratings tie: 24 scenarios compared, **Gemini 12 wins, Seedance 12**,
no ties. Seedance leads image-to-video 7 to 5 (89.6% vs 83.9% mean rating);
Gemini leads the edits 7 to 5 (84.0% vs 77.3%). Reliability, the worst single
rating: Gemini 50%, Seedance 22.5%. Lowest cells worth a look: the pedestrian
removal (Gemini 50%, edit not performed), the boundary catch (Gemini 58%,
cuts change the fielder), the kill-feed blur (Gemini 60%, source footage not
used), and on Seedance the smoothing edit (22.5%, ghosting) and the buggy
removal (42.5%, dust trail left behind). Plain-language observations are in
`docs/2026-09-17-gaming-video-observations.md`; the client and internal
reports follow the layout conventions of `runs/merged-video-all/report-client.html`.

Run results checklist, ticked where the evidence exists:

- [x] Every scenario has an output from every enabled model, or a recorded reason (49 outputs, 1 recorded refusal).
- [x] Every video output plays with a duration (all probed; 8 s image-to-video, about 6 s edits).
- [ ] First frame of each image-to-video output matches its still: reviewer to tick from the report.
- [ ] "Must stay" items unchanged on edits: reviewer to tick from the report.
- [x] The judge scored every output: 49 of 49 scored; one Seedance edit needed a fourth judge attempt with a 900 s timeout (gap log 21).
- [ ] The report opens and shows all three games and both arms: built, not yet opened by a reviewer.
- [ ] Blind review: the judge saw shuffled A/B labels, but the runner names output files by model id, so file names do reveal the model. Gap for the plan.
- [x] Cost within budget: $110.12 of 220.
- [x] Every failure listed with scenario, model and reason (Cells tab).

## Running the scenarios (steps 11 and 12)

### Before the run

- Ravi approves a budget figure for each lane. Nothing runs before that.
- Run a dry run first, so the runner reads every scenario file without
  calling any model. Fix any file that fails to load.
- Confirm which models are enabled in each lane. Today two video models and
  two image models are enabled. Do not enable anything else for the pilot.

### The run

From the `video/` folder:

```bash
python -m runner.cli run --modality video --scenarios scenarios/bank-video-gaming --budget <approved figure>
```

From the `image/` folder:

```bash
python -m runner.cli run --modality image --scenarios scenarios/bank-image-gaming --budget <approved figure>
```

Then for each run id the runner prints:

```bash
python -m runner.cli judge --run <run-id>
```

```bash
python -m runner.cli report --run <run-id> --open
```

### Run results checklist

The Reviewer ticks each line.

- [ ] Every Final scenario has an output file from every enabled model, or a recorded reason (refused, failed, over budget).
- [ ] Every video output plays, has the expected length, and is not blank or black.
- [ ] Every image output opens, is the expected size, and is not blank.
- [ ] For Image-to-Video: the first frame matches the input picture.
- [ ] For Video Editing and Image Editing: the "Must stay" items are unchanged when you look at input and output side by side.
- [ ] The judge has scored every output, and the "How to judge" items on the card match what the judge was asked.
- [ ] The report opens and shows both games, both tasks, and every enabled model.
- [ ] No output file name or report label reveals which model made it (blind review must hold).
- [ ] The cost report is within the approved budget for each lane.
- [ ] Every failure is listed with the scenario ID, model, and reason.

## What we are looking for

The pilot is a test of the plan, not just of the models. While doing each
step, everyone writes down anything that was:

- **Unclear** — a plan instruction that two people read differently.
- **Slow** — a step that took much longer than expected, and why.
- **Missing** — something we needed that the plan does not mention.
- **Wrong** — a plan rule that gave a bad result when followed.
- **Impossible** — something the plan asks for that we could not do (no
  source found and could not generate one, a test type that no moment in the
  game fits, a scenario the runner cannot express).

## Gap log

Kept at the bottom of each sheet on a tab called **Gap log**, and copied here
when the pilot ends.

| # | Step | What happened | Effect | Fix or decision | Owner | Done |
|---|---|---|---|---|---|---|
| 1 | | | | | | |

## Exit: when do we scale to the other 16 games?

All of these must be true:

1. Both lanes have a final set of 5–7 scenarios per game, approved by Ravi.
2. Both sheets are complete: every column filled on every Final row, Game
   briefs tab present, Gap log tab present.
3. Both runs finished, both reports open, and the run results checklist is
   fully ticked.
4. Every gap in the gap log has a fix written into the plan or a decision to
   accept it.
5. Ravi has said go.

If any of these fails, we fix the plan and repeat the failing steps on the
same two games. We do not add games until the pilot passes.

## Rough timeline

| Days | What |
|---|---|
| 1 | Steps 1–2: confirm games, write both game briefs |
| 2–3 | Steps 3–4: scenario cards and sources, both lanes in parallel |
| 4 | Steps 5–8: relevance check, duplicates, scoring, ranking |
| 5 | Step 9: Ravi reviews scenarios. Fixes if needed |
| 6 | Steps 10–11: scenario files, dry run, budget approval, run |
| 7 | Steps 12–13: validate results, Ravi reviews results |
| 8 | Step 14: gap review, plan update, decision to scale |

About eight working days if nothing is sent back. Add two for one round of
fixes.
