# Gaming scenarios: human observations

Video module, 25 scenarios in total across BGMI, Real Cricket 24 and GTA V: 13 image to video and 12 video edits, one clip per scenario from each model, Gemini Omni Flash and Seedance 2.5. These are notes from a person on our team who watched the clips in the gaming video pilot report, written 18 September 2026. They cover where Gemini wins, what its strong areas are, and where it loses.

"Gemini" is the model the report labels Omni Flash. The percentages are the report's ratings from the blind judge (Gemini 3 Flash); the descriptions are what was seen on screen. Each note describes Gemini's clip first, then gives both ratings, with the reason for Seedance's rating where one was noted.

Of the 25 scenarios, 24 were decided: Gemini won 12 and lost 12. The other one, Dugout celebrates a wicket (Real Cricket 24), was not compared: Seedance refused the input still, which its privacy filter flagged for the group of photoreal faces. Gemini rated 92% on it.

## Where Gemini wins

Gemini won 12 scenarios: 5 image to video and 7 video edits. It also wins every headline measure: quality, reliability, cost per clip, latency mean, latency p50 and latency max.

| Measure | Gemini | Seedance 2.5 |
|---|---|---|
| Quality, mean rating | 83.9% | 83.4% |
| Reliability, lowest rating on any one scenario | 50.0% | 22.5% |
| Cost per clip | $1.10 | $3.44 |
| Latency mean | 89.9 s | 260.2 s |
| Latency p50 | 85.6 s | 229.9 s |
| Latency max | 133.8 s | 483.4 s |

### Image to video: 5 wins

- **Jeep over the rock, stop, smoke, revive** (BGMI). All five actions happened in the right order: over the rock, stop, doors open, smoke, revive. Gemini 90%. Seedance 85%.
- **Airdrop lands, sniper crawls** (BGMI). The crate landed, the parachute collapsed, and the smoke drifted realistically while the sniper crawled forward. Gemini 99%. Seedance 98%.
- **Score bar updates after a six** (Real Cricket 24). Only the score bar changed, the frozen scene stayed frozen, and the numbers were right. Gemini 100%. Seedance 84%, because it got the numbers wrong.
- **Fan in the stands, mid-clap** (Real Cricket 24). The clapping was smooth, and the face stayed the same person throughout. Gemini 94%. Seedance 91%.
- **Character intro push-in at dusk** (GTA V). The camera pushed in while the character stayed identical throughout. Gemini 100%. Seedance 97%.

### Video edit: 7 wins

- **Remove the buggy in the final circle** (BGMI). The buggy and its dust trail disappeared from every frame. The soldiers in front stayed untouched. Gemini 100%. Seedance 43%, because it left the dust trail behind.
- **Slow-motion edge to slip** (Real Cricket 24). Both clips show the same action, there is no difference between them, but Gemini wins. Gemini 90%. Seedance 50%. See the note on this scenario at the end.
- **Add a bowling speed readout** (Real Cricket 24). The readout appeared in the corner at the exact moment the ball left the bowler's hand. Nothing else changed. Gemini 100%. Seedance 90%.
- **Smooth a choppy six** (Real Cricket 24). The jerky phone recording became smooth, with some ghosting around the fast-moving ball. Gemini 83%. Seedance 23%, because its result was still jerky and lost picture quality.
- **Day drive turned to night** (GTA V). One of the whole-look edits in the strong areas below: day became night and everything else stayed in place. Gemini 100%. Seedance 60%; the judge noted that it rebuilt the street instead of relighting it.
- **Streetwear to a suit** (GTA V). One of the whole-look edits in the strong areas below: a new outfit, with everything else in place. Gemini 99%. Seedance 96%.
- **Summer street to holiday snow** (GTA V). One of the whole-look edits in the strong areas below: summer became snow and everything else stayed in place. Gemini 100%. Seedance 94%; the judge noted that the visible breath was missing.

## Gemini's strong areas

- **Changing the whole look of a clip in one consistent way while everything stays in place:** day to night, summer to snow, a new outfit. All rated 99 to 100%.
- **Adding or removing something cleanly:** the speed readout, the score bar, the buggy and its dust trail. All 100%.
- **Changing timing:** slow motion and smoothing a choppy recording. Seedance failed both, Gemini did both well. *The slow-motion half needs a second look: see the note at the end.*
- **Bringing a still picture to life with one clear movement:** the camera push-in, the airdrop, the reload, the clapping fan, the dugout celebration. All 90% or more.

## Where Gemini loses

Gemini lost 12 scenarios to Seedance 2.5: 5 video edits and 7 image to video. Each note says what went wrong in Gemini's clip.

### Video edit: 5 losses

- **Remove the pedestrian** (GTA V). The clip came back untouched. The pedestrians on the right were still there in every frame. Gemini 50%. Seedance 83%: it removed the pedestrian and rebuilt the wall behind her, with some shimmer on the bricks.
- **Blur names in kill feed and tags** (BGMI). The blurs were clean and steady, but they were applied to a different moment of the same match, not the clip supplied. The score, the clock and the player names all differ. Gemini 60%. Seedance 95%: it blurred the clip it was given.
- **Rifle becomes frying pan** (BGMI). The pan was placed in the hand and tracked perfectly, but the grass, the lighting, and the camera framing were all redrawn. Gemini 73%. Seedance 100%: it changed only the object.
- **Coastal drive turned to sunset** (BGMI). The sunset light and long shadows were right, but the car's badge, lettering, and tail-light detail were simplified away. The car had to stay unchanged. Gemini 73%. Seedance 100%: it relit the scene and kept the car as it was.
- **Recolour the batting kit on the run** (Real Cricket 24). The kit turned yellow as asked, and the logos were updated to match, but a blue halo stayed around the players where the old kit met the grass, and the green trim flickered. Gemini 81%. Seedance 95%.

### Image to video: 7 losses

- **Ball in the air, boundary catch** (Real Cricket 24). The catch was told through several cuts. The fielder's face changed from one shot to the next and the jersey text warped. Gemini 58%. Seedance 82%: it kept one view.
- **Batsman hits the ball out of the boundary** (Real Cricket 24). The shot became a montage of angles. The batter's face, beard, and kit logos changed, and the stadium's boards and lighting shifted between angles. Gemini 66%. Seedance 80%.
- **First-person night chase on foot** (GTA V). The brief asked for a first-person view with no cuts. Gemini showed a runner in front of the camera, cut at the four-second mark, and the walls and ground warped as the camera moved. Gemini 67%. Seedance 85%.
- **Helicopter searchlight on the rooftop** (GTA V). The rooftop, outfit, and lighting matched, but the character slid across the roof instead of running and did an impossible jerky flip halfway through. Gemini 70%. Seedance 82%.
- **Mass jump from the plane** (BGMI). Extra cargo planes of different shapes appeared in mid-air and flew through the formation, and some jumpers drifted upwards. Gemini 79%. Seedance 100%: it kept a single plane and a clean jump.
- **First-person rifle reload** (BGMI). † A narrow loss on one of Gemini's strong one-movement clips. The judge saw slight finger distortion as Gemini's hand pulled the charging handle and none in Seedance's clip. Gemini 95%. Seedance 99%.
- **Heist getaway out of the bank** (GTA V). † The judge noted that Gemini's robbers ran without weight and clipped into the car seats, and that the banknotes floated in straight lines; Seedance's run and camera were smooth, though its car doors started closed when the brief said open. Gemini 83%. Seedance 94%.

## Note on the slow-motion scenario

**Slow-motion edge to slip** is counted above as a Gemini win because the judge rated Gemini 90% and Seedance 50%. The judge wrote that Gemini held the impact in slow motion for two seconds and that Seedance applied no slow motion at all. On viewing, both clips show the same action at the same speed, with no visible difference between them.

A frame-by-frame check supports the viewer. Each frame of each output was matched to its nearest frame in the source clip. Gemini's 144 frames line up one to one with the source's 144, a playback rate of 1.00x in every half second, with no slowed stretch anywhere. Seedance's clip is the source at a lower resolution with 7 frames dropped evenly (137 of 144). Neither model applied slow motion, so Gemini's 90% rests on an effect that is not in its clip.

Until this scenario is re-judged it reads better as undecided than as a Gemini win. That would make the tally Gemini 11, Seedance 12, one open, and the video edits 6–5 to Gemini.

---

† No viewing note yet for this scenario. The rating is from the report and the description is the judge's, not yet confirmed by a viewer.
