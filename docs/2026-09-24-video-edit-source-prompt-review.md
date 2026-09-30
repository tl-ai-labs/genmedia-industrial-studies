# Video edits: do the source-clip prompts match the edit prompts?

Review of the 12 video_edit scenarios in `video/scenarios/bank-video-gaming/`, 24 September
2026. Two prompts exist for every edit scenario, and only one of them was ever sourced or
reviewed:

| | The edit prompt | The generation prompt |
|---|---|---|
| What it is | The instruction the two models were judged on | The text-to-video prompt that made the six-second source clip the edit was applied to |
| Where | `bank-video-gaming/VID-GEDIT-nn.yaml`, `prompt:` | `gen-inputs-gaming/GEN-VID-GEDIT-nn.yaml` (01 to 08) and `gen-inputs-gaming-gta-console/` (09 to 12), `prompt:` |
| Public source | Yes, all 12: header line `# Prompt source:`, and the register `docs/sheets/gaming-video-prompt-sources.xlsx` | None. No origin line, no source line, not in the register |
| Who wrote it | A real person, on the page cited | The team, from the card's "Input assets required" column |
| Model that ran it | Omni Flash and Seedance 2.5 | Veo 3.1 on Vertex, runs `2026-09-17_101040_video` and `2026-09-17_120543_video` |

## 1. The sourcing question

The check covered every generation scenario file in both lanes: 16 for the video clips
(including the four superseded GTA versions) and 20 for the stills that the image-to-video
scenarios start from. Not one carries a `# Prompt origin` or `# Prompt source` line, and the
sheet's only provenance for them is the "Generation details" column, which records the model,
the run and the date, not where the words came from.

This is not a breach of the plan. Section 4.2 of `docs/2026-09-14-gaming-scenario-plan.md`
applies the real-prompt rule to the **Prompt line**, the thing the study evaluates. Step 4 and
section 5 say an input may be created when no public one exists, and
`docs/2026-09-16-pilot-asset-collection.md` records every generated input as a placeholder
until an own screenshot replaces it. The generation prompt is a description of the placeholder,
not a thing under test.

It is a documentation gap. Nothing in the repository says the generation prompts are
team-written, and a reader of the register could assume "every prompt has a public source"
covers them. Two fixes, both in this review:

- Every generation file gets a header line: `# Prompt origin: Team-written, from the card's
  "Input assets required" line. Not evaluated; no public source.`
- The register gets a tab, "Generation prompts", listing all 25 with that origin, the model,
  the run and the review verdict below.

## 2. Pair review: does each source clip contain what its edit needs?

Method: for each scenario, take every element the edit prompt and its five adherence clauses
depend on, and check that the generation prompt asks for it. Then check what the judge saw in
the clip, which tells us what Veo actually produced. Verdicts: **match**, **gap** (the prompt
missed a detail the edit relies on), **fail** (the clip cannot support the test).

| # | Scenario | Verdict | What the edit needs from the source | What the generation prompt says | What the clip actually had |
|---|---|---|---|---|---|
| 01 | Rifle becomes frying pan | match, one gap | A rifle visible in both hands in every frame; a running cycle; third-person view | Rifle in both hands, running, grassland. **Camera angle omitted** (card says third-person) | As asked |
| 02 | Blur names in kill feed and tags | **gap** | Kill feed with readable names; name tags on moving players; the "keep everything else sharp" clause needs the rest of the HUD present and named: squad list, minimap, health bar (all in the card's "Must stay") | Kill feed and name tags only. The boilerplate then says "no on-screen display unless stated", which contradicts a full HUD | Kill feed, name tags, a squad list with names, a score and a match clock. Seedance blurred the squad-list names too and was marked down for it; the ambiguity was ours |
| 03 | Coastal drive turned to sunset | gap | A car whose geometry, badges and colour can be checked as unchanged; midday light so the change to sunset is visible | Four-door sedan, midday sun, "no logos". **Camera angle omitted** (card says chase view from behind) | A car with a real badge and "Insignia"/"Turbo" lettering (gap 18). Omni Flash lost points for altering the badge it should never have had |
| 04 | Remove the buggy | match | One moving vehicle with a dust trail; prone players that must stay | Buggy left to right kicking up dust, three prone players. Camera angle omitted | As asked |
| 05 | Slow-motion edge to slip | match | One clearly visible moment of bat contact, mid-clip, then a slip catch | Edge and catch at chest height. Camera angle omitted; no timing of the contact | As asked. The test failed for a different reason: neither model slowed anything and the judge said one did (18 Sep frame check) |
| 06 | Add a bowling speed readout | match | Release moment visible; bottom-right corner free of anything the graphic would cover; no existing graphics | "No graphics on screen", batter defends. Camera angle omitted (card says from behind the bowler) | As asked |
| 07 | Recolour the batting kit | match, one gap | Two batters in blue kit **and** blue pads, gloves and helmets, since clause 3 checks those stay unchanged | Blue kit with white trim, running, sliding. **Pads, gloves, helmets not mentioned**; side-on view omitted | Pads, gloves and helmets present and blue, by luck |
| 08 | Smooth a choppy six | **fail** | A source that is actually choppy. The card's own input plan says "deliberately captured at a low frame rate (15 fps) so the input really is choppy" | "Steady camera, continuous single shot". Nothing asks for choppiness, and nothing was done afterwards | **24 fps, 144 frames, zero duplicated frames, identical to the other eleven clips.** There was nothing to smooth. The judge's "transforming the choppy source into a smooth video" describes a source that did not exist, the same failure as GEDIT-05 |
| 09 | Day drive turned to night | gap | Daylight street with reflective surfaces; no on-screen display | As asked, "no on-screen display" | The judge on the Seedance cell refers to "the UI speedometer values" in the source, so the clip carried a speedometer despite the prompt. Not confirmed by eye; needs a look |
| 10 | Remove the pedestrian | **gap** | Exactly one person on the right of the background, unmistakable, so "remove the person" has one answer; clause 5 says "other pedestrians untouched", so the card itself expected more than one | "One pedestrian NPC walks through the right side of the background" | Several: the judge on the Omni Flash cell writes "all background pedestrians on the right side remain", and on the Seedance cell identifies the removed one as "the woman in the red jacket". The edit was ambiguous in the clip we supplied |
| 11 | Streetwear to a suit | match | Hoodie and jeans; face and hair visible; a walk and a kerb step | As asked | As asked |
| 12 | Summer street to snow | match | Leafy trees, parked cars, sunlight; a subject whose wardrobe can be checked | As asked | As asked |

Two things run across all twelve:

- **The camera angle in the card's "Input assets required" line was dropped from every
  generation prompt** (third-person, chase view from behind, broadcast view from behind the
  bowler, side-on). The clips happened to come out at usable angles, but that was Veo's choice,
  not ours, and an edit that says "camera path stays as filmed" is easier to judge when we
  specified the path.
- **The style boilerplate is doing two jobs at once.** "No on-screen display unless stated"
  is right for ten scenarios and wrong for GEDIT-02, where the HUD is the subject of the edit.
  The rewrite below lists HUD elements explicitly where they are wanted and forbids them
  explicitly where they are not.

### What this means for the results already published

- GEDIT-08 joins GEDIT-05 as a scenario whose recorded result cannot stand: 83% against 23%
  for a smoothing that had nothing to smooth. Read fairly the batch is now Omni Flash 10,
  Seedance 12, two undecided, and video edits 5 to 5 with two open. Gap log 22.
- GEDIT-10 and GEDIT-02 stand as results, but each carries an asterisk: part of the loss on
  GEDIT-10 (Omni Flash 50%) and part of the Seedance mark-down on GEDIT-02 came from
  ambiguity in the source we supplied, not only from the models.
- GEDIT-03's badge (gap 18) cost Omni Flash on a clause it should not have faced.

## 3. The rewritten generation prompts

The edit prompts are not changed: they are sourced, and changing them would break the
provenance the register documents. Only the generation prompts are rewritten. Each one now
follows the same shape, in this order: camera, subject and action, setting and light, the
elements the edit depends on, on-screen display (listed or forbidden), style, and duration.
Every element a clause checks is named. Real brands and names are excluded by wording, not
just by the boilerplate.

The files are in `video/scenarios/gen-inputs-gaming-v2/`, one per scenario, ready to run
with `configs/models-gen-inputs-temp.yaml`. They have not been run. GEDIT-08 also needs a
post-step, written into its file.

**GEN-VID-GEDIT-01**, for "swap the rifle for a frying pan"
> Third-person view from behind and slightly above, camera following at a fixed distance. One player in olive-green military gear runs at a steady pace across open grassland, holding a wooden-stock assault rifle in both hands with the muzzle forward; the rifle stays fully visible in every frame. Overcast daylight, long grass, low hills behind. No other characters, no vehicles. No on-screen display of any kind. Realistic mobile-game render look, sharp and clean, no text, no logos, no watermark. Steady camera, one continuous shot, six seconds.

**GEN-VID-GEDIT-02**, for "blur the names in the kill feed and the name tags above players, in every frame, and keep everything else sharp"
> Third-person view over the player's shoulder. A squad firefight beside a two-storey house. The full match display is present and legible: a kill feed in the top-left corner listing player names, name tags floating above two teammates who move across the frame, a squad list with four names under the kill feed, a circular minimap in the top-right corner, a health bar at the bottom centre, and a match timer at the top centre. All names are fictional gamertags in Latin letters, such as RIVERSIDE_07, DUSKFOX and MARLOW-9; no real brands, no real player names. Muzzle flashes and smoke. Realistic mobile-game render look, sharp and clean, no watermark. Steady camera, one continuous shot, six seconds.

Reviewer note: the edit prompt says "keep everything else sharp", and the clip now has
squad-list names as well. Either the reviewer accepts that the squad list is "everything
else" and stays sharp, which is the reading the judge applied, or the Real-based edit prompt
gains the words "leave the squad list, minimap and health bar sharp", recorded in "Other
source details" like every other adaptation.

**GEN-VID-GEDIT-03**, for "change the lighting in this scene to sunset"
> Chase view from behind the car, camera following at a fixed distance. A plain, unbranded four-door sedan in dark grey drives at moderate speed along a two-lane coastal road in bright midday sun: sea and a low guard rail on the left, grass hills on the right. No badge, no lettering, no number-plate text; a plain grille and plain tail-lights. Short midday shadows directly under the car. No other vehicles. No on-screen display of any kind. Realistic mobile-game render look, sharp and clean, no text, no logos, no watermark. Steady camera, one continuous shot, six seconds.

**GEN-VID-GEDIT-04**, for "remove the car"
> Third-person view from behind and above the players. Three players in military gear lie prone in tall grass inside a small final zone, still. In the background an open-frame buggy drives across the frame from left to right, kicking up a visible dust trail that hangs in the air behind it. The buggy is the only vehicle and, apart from the grass moving in the wind, the only moving thing. Sky and low hills behind. No on-screen display of any kind. Realistic mobile-game render look, sharp and clean, no text, no logos, no watermark. Steady camera, one continuous shot, six seconds.

**GEN-VID-GEDIT-05**, for the slow-motion edit at the moment the ball touches the bat
> Broadcast view from behind the bowler's arm, fixed camera at stump height. The bowler delivers, the batter plays forward and the ball takes a clear, visible edge of the bat around the middle of the clip, then flies to first slip, where the fielder takes the catch at chest height. The ball stays in frame throughout. No graphics or score bar on screen. Realistic mobile-game render look, sharp and clean, no text, no logos, no watermark. One continuous shot, six seconds.

**GEN-VID-GEDIT-06**, for "add a broadcast speed readout graphic, 141 km/h, bottom right, fading in as the ball leaves the hand"
> Broadcast view from behind the bowler's arm, fixed camera at stump height. A fast bowler runs in and delivers; the ball leaves the hand about two seconds in, and the batter defends it back down the pitch. The bottom-right corner shows only pitch and outfield. No graphics of any kind on screen: no score bar, no speed readout, no ball-tracking line. Realistic mobile-game render look, sharp and clean, no text, no logos, no watermark. One continuous shot, six seconds.

**GEN-VID-GEDIT-07**, for "replace both batters' outfit with a yellow cricket kit with dark-green trim"
> Side-on view from square of the wicket, camera panning to follow the runners. Two batters in matching plain blue kit with white trim, wearing blue helmets, blue pads and blue gloves, run between the wickets and slide their bats over the crease. The kit carries no sponsor marks or names. Green outfield, crowd blurred behind. No on-screen display of any kind. Realistic mobile-game render look, sharp and clean, no text, no logos, no watermark. One continuous shot, six seconds.

**GEN-VID-GEDIT-08**, for "use motion interpolation to avoid frame stutter across the whole clip"
> Broadcast view from behind the bowler, the camera tilting up to follow the ball. The batter lofts the ball high for six and the camera follows it over the boundary into the crowd. No graphics or score bar on screen. Realistic mobile-game render look, sharp and clean, no text, no logos, no watermark. One continuous shot, six seconds.
>
> **Post-step, required.** A text-to-video model will not render a choppy clip on request. After generation, make it choppy: keep every second frame and hold each for two frames (`ffmpeg -i in.mp4 -vf "fps=12,fps=24" -c:v libx264 -crf 18 out.mp4`), so the clip plays at 24 fps with visible 12 fps stutter, the same duration, and no other change. Verify with a duplicate-frame count (expect about 72 of 144) before copying it to the bank, and record the command in Generation details.

**GEN-VID-GEDIT-09**, for the day-to-night relight
> Third-person chase view from behind the car, camera following at a fixed distance. A dark-red muscle car drives at steady speed down a straight palm-lined city boulevard in bright daylight, shopfronts with awnings and parked cars on both sides, glossy paint and chrome trim that will show reflections. No on-screen display of any kind: no speedometer, no minimap, no wanted stars, no radar. Realistic open-world console-game render look, sharp and clean, no text, no logos, no watermark, no touch controls, no virtual joystick, no buttons. Steady camera, one continuous shot, six seconds.

**GEN-VID-GEDIT-10**, for "remove the person walking through the right side of the background"
> Third-person view from behind the protagonist, camera following. The protagonist walks along a city sidewalk beside a long, plain shopfront wall. Exactly one other person is in the shot: a pedestrian in a bright red jacket walks the opposite way through the right side of the background, passing in front of the wall, fully visible from the first frame to the last. The rest of the sidewalk and the road are empty; no cars, no other people. No on-screen display of any kind. Realistic open-world console-game render look, sharp and clean, no text, no logos, no watermark, no touch controls, no virtual joystick, no buttons. Steady camera, one continuous shot, six seconds.

**GEN-VID-GEDIT-11**, for the change to a charcoal suit
> Third-person view from the front-left, camera tracking alongside. The protagonist, a man in a plain grey hoodie and blue jeans with white trainers, walks briskly along a city street and steps up onto a kerb; face, hair and hands are clearly visible throughout and he does not speak. Daylight, a few parked cars, no other people. No on-screen display of any kind. Realistic open-world console-game render look, sharp and clean, no text, no logos, no watermark, no touch controls, no virtual joystick, no buttons. One continuous shot, six seconds.

**GEN-VID-GEDIT-12**, for the summer-to-winter change
> Third-person view from behind, camera tracking. The protagonist in a plain T-shirt and trousers walks along a sunny residential street: cars parked along the kerb, deciduous trees in full summer leaf, low houses, hard sunlight and short shadows. No other people. No on-screen display of any kind. Realistic open-world console-game render look, sharp and clean, no text, no logos, no watermark, no touch controls, no virtual joystick, no buttons. One continuous shot, six seconds.

## 4. Gap log additions

| # | Step | Gap | Impact | Fix | Owner | Status |
|---|---|---|---|---|---|---|
| 22 | 4 | The source clip for VID-GEDIT-08 is a smooth 24 fps clip with no duplicated frames, though the card requires a choppy 15 fps recording and the generation prompt asked for a steady continuous shot. Nothing was done after generation to make it choppy. | The smoothing test had nothing to smooth; the recorded 83% vs 23% describes a source that did not exist, as with VID-GEDIT-05. Fair tally becomes Omni Flash 10, Seedance 12, two undecided. | Regenerate with the v2 prompt and the frame-drop post-step; re-run the pair. | Collector | Open |
| 23 | 4 | Generation prompts carry no origin marker and the register does not list them; every one also dropped the camera angle from the card's Input line, and GEDIT-02 and GEDIT-10 omitted elements the edit clauses depend on (full HUD; exactly one pedestrian). | Readers may assume the real-prompt rule covers them; two edit results carry ambiguity we introduced. | Origin header line on every generation file; "Generation prompts" tab in the register; v2 prompt set in `gen-inputs-gaming-v2/`. | Collector, reviewer | Open |
