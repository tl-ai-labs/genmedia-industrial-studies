# panel — blind human panel + judge correlation

One panel across **image, video and voice**: a page that shows a reviewer two
outputs of the same brief with the model names hidden, records a thumbs-up
on the better one, and a script that reports how often the human majority
agrees with the LLM judge — **per lane and overall**. That agreement number is
the deliverable from the 4 September review: *"that defines whether your AI
is aligned to human judgment."*

Free, offline, stdlib + PyYAML. Nothing here can spend.

## The three commands

```bash
cd panel
/opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest          # 39 tests, no keys, no network

# 1. export: copy run media to opaque ids, write dist/ (public) + private/key.json
.venv/bin/python -m runner.cli export \
    --run image=../image/runs/<run-id> \
    --run video=../video/runs/<run-id> \
    --run voice=../voice/runs/<run-id>          # when the voice runs are on this machine
    # ...or, from the committed dashboard export (17 items, one per scenario):
    #   --voice-dashboard ../voice/dashboard [--voice-items all]

# 2. serve: the page + the one endpoint that appends a vote
.venv/bin/python -m runner.cli serve --port 8765        # http://<this-machine>:8765/

# 3. correlate: human majority vs judge, per lane and overall
.venv/bin/python -m runner.cli correlate --out correlation.md --json correlation.json
# ...or as one self-contained HTML page (the studies console embeds the panel's
#    lanes at /unpublish/genmedia-blind-panel; it does not show this snapshot):
.venv/bin/python -m runner.cli correlate --html results.html --json correlation.json
```

`--run` is repeatable and takes any lane's run folder — the panel reads the
folder contract every lane already writes (`manifest.json`, `scenarios/`,
`scores.jsonl`, `outputs/<modality>/<scenario>/<model>.<ext>`) and imports no
lane code. A run with more or fewer than two arms on a scenario, or with an
arm whose media is missing, skips that scenario and says why.

## How the blind is kept

Every exported filename in every lane names its model. So the exporter:

- copies each file to `dist/media/item-<salted hash>.<ext>`, and each input
  (an edit's source, an ad's reference still) to `input-<hash>.<ext>`;
- lists the two arms of an item **sorted by that hash**, so "first in the
  pair" carries no model information; the page then flips left/right per
  reviewer on top (deterministic per reviewer + item, so a reload shows the
  same order);
- strips the metadata that could name a producer without re-encoding a
  pixel: PNG ancillary chunks (`tEXt`, `iTXt`, and the `caBX` chunk that
  carries a C2PA "made with…" manifest), JPEG APP1–APP15 and COM, WAV `LIST`
  and `id3`, MP3 ID3v1/v2, and MP4 `udta`/`meta` boxes (renamed to `free`
  and zeroed in place, so sample offsets stay valid);
- writes the id→model mapping to `private/key.json`, which the server reads
  and never serves (it refuses to start if the key sits inside `dist/`).

`tests/test_export.py::test_nothing_public_names_a_model` walks everything
under `dist/` — names and bytes — for every model id. If that test fails the
panel is not blind.

The page itself never sees a model name. It posts `{reviewer, item, left,
right, pick, reason}`; the **server** resolves the models from the key and
writes the full record. Nothing in devtools, a hover, or a saved file names
the model — and when the server hands a reviewer their earlier picks back
(`GET /api/votes?reviewer=`), it does so as the picked *media* id, never the
model. The one exception is `GET /api/results`, written for the studies
console's results page (who reviewed what, and why): it names the models, so
it is for the study team, and a reviewer should vote before they look.

What is *not* hidden, and cannot be: the content. A reviewer who knows the
models' house styles can guess. That is the same limit the LLM judge's
blinding has, and it is why the correlation is reported with its n.

## What a vote looks like

`votes.jsonl`, one record per vote, appended by the server:

```json
{"ts": "2026-09-11T10:14:02.118Z", "reviewer": "sai", "lane": "image",
 "run_id": "2026-09-01_224335_image", "scenario_id": "IMG-TXT-01",
 "picked": "gpt-image-2-high", "over": "gemini-3-pro-image-vertex",
 "reason": "the headline is legible"}
```

That is the whole record (Sai, 2026-09-11: "we just need votes and reasons
— when, reviewer, lane, scenario id, over, reason"): no item id, no side, no
media id, no email. `run_id` stays because the correlation needs it to find
the judge's scores. A "can't tell" stores `null` for both models. Lines
written before 2026-09-11 carry the older shape (`pick`, `left_model`,
`right_model`, `picked_model`, an `item`) and are read as they are — the file
is append-only and never rewritten; only those older lines can feed the
"Left picks" position-bias column, so it reads "—" once they are outnumbered.

The page offers no "can't tell" (a forced choice, at the study lead's request
on 10 Sep); the server and the correlation still accept `pick: "tie"` with
`picked_model: null` should that be reinstated. The file stands on its own — the
correlation never needs the key — and it is small enough to commit, which is
why it is not gitignored while `dist/` and `private/` are. A reviewer who
revisits an item and votes again appends a second record; the correlation
keeps the **last** vote per (reviewer, scenario). The reviewer is the
**name** (2026-09-11, Sai: "name is fine, no need email"). `reason` is
optional free text, trimmed, at most 500 characters, `null` when none was
given.

## What the correlation reports

Per scenario, the **human verdict** is the arm with more picks (equal = tie;
"can't tell" counts for neither side). The **judge verdict** is the arm with
the higher `scores.jsonl` score, unless the gap is inside the lane's own tie
band — the same `TIE_BAND` each lane's scoring uses (image 0.5, video 0.5,
voice 0.05), so the judge is held to the verdict its report printed.

| Column | Meaning |
|---|---|
| Agreement (decided) | Of scenarios where **both** the panel and the judge picked an arm, how often the same one. The headline, with a Wilson 95% interval because n is tens. |
| 3-way, κ | Agreement counting ties as a verdict, and Cohen's kappa for it. |
| Spearman ρ | Human margin (share of decisive votes for the reference arm) against the judge's score gap. Does the judge's *strength* of preference track the panel's. |
| Can't tell | Share of all votes that were "can't tell". |
| Left picks | Share of decisive votes for whichever side was on the left. Far from 50% means position, not quality, drove votes. |

The reference arm is the Gemini/Omni one when there is exactly one; only the
sign convention depends on it.

A poor number is a finding. Our own reviewers found Gemini's voice more
soothing while the judge scored it lower; if that shows up here it is
evidence about the rubric, and the per-scenario table prints every
disagreement in bold rather than smoothing it.

## Running a session

- Export from the run(s) the report used, serve on a machine the reviewers
  can reach (LAN or a tunnel), send the URL — or point them at the studies
  console's `/unpublish/genmedia-blind-panel`, which renders this same
  panel itself against this server (CORS is on for that). Reviewers give
  a name once (or arrive with `?reviewer=`); the page starts them at the first
  item they have not answered, and an item they HAVE answered shows the
  side they chose and the reason they gave, so a return visit — any day,
  any device, same email — picks up where it left off. An optional reason
  box sits under the pair; on an answered item "Save reason" re-posts the
  same pick with the new text.
- Keys: `←` left is better, `→` right is better, `shift+←/→` to move without
  voting.
- **Industry**: every item carries the primary industry from its lane's
  `configs/industry_map.yaml` (voice: the dashboard's own label). The
  header menu filters the current lane to one industry, so a reviewer can
  cover just their field; lane counts and progress stay per lane.
- Video and voice previews come from the run folder as the lane wrote them:
  the video lane's `previews/` (ffmpeg re-encodes, ~0.5 MB) are preferred
  over the 2–7 MB originals when present — run the lane's report with
  `--self-contained` first.
- Re-exporting wipes `dist/` and draws a fresh salt; existing votes stay
  valid because each record carries its resolved models.

## Hosting it (Cloud Run + one bucket)

The studies console on Vercel renders the panel itself and talks to THIS
server over HTTPS, so the server has to be somewhere reviewers can reach and
the votes on a disk that outlives a restart. `deploy/cloud-run.sh` does both
(Sai, 2026-09-11: "gcloud is best, we already have the billing"):

```bash
gcloud auth login                       # an account on ai-studies-console
deploy/cloud-run.sh                     # first time / after a code change: bucket + image + service
deploy/cloud-run.sh --sync              # after a re-export: items.json + key up, no rebuild
deploy/cloud-run.sh --votes             # hosted votes.jsonl down, to compare and commit
deploy/cloud-run.sh --url               # the URL the console needs as VITE_PANEL_URL
```

What it makes, all in `us-central1`, all inside Google's always-free tier at
this scale:

| Piece | Holds | Free tier | This panel |
|---|---|---|---|
| Cloud Storage bucket `genmedia-panel-<project>` | `dist/items.json`, `dist/index.html`, `private/key.json`, `votes.jsonl` — **no media** | 5 GB regional (US) | a few hundred KB |
| Cloud Run service `genmedia-panel` | the code, scale-to-zero, **max 1 instance** | 2 M requests, 180 k vCPU-s, 360 k GiB-s a month | well inside |
| Artifact Registry repo | the image (code only, ~50 MB) | 0.5 GB | inside |
| Cloud Build | builds the image | 120 build-minutes a day | inside |

**No media in the cloud.** The pairs' files — opaque, content-hashed names
that name no model — are copied verbatim into the studies console at
`apps/dashboard/public/reports/genmedia-blind-panel/media/` (with
`items.json` beside them) and served by Vercel from there, both to the
console's own rendering of this panel and to this page when hosted:
`GET /api/config` returns `media_base`, from the `PANEL_MEDIA_BASE`
environment variable the deploy sets (a laptop session leaves it unset and
serves its own `dist/media`). After a re-export, copy `dist/media` and
`dist/items.json` to the console and commit them there along with `--sync`.

**One instance, on purpose.** The bucket is mounted with Cloud Storage FUSE,
where a file append is a rewrite of the whole object; two instances appending
to `votes.jsonl` at once would overwrite each other's lines. `--max-instances 1`
plus the server's own lock keeps every vote. At scale-to-zero the first
request after a quiet spell takes a couple of seconds to start.

**Public access.** The script first tries to switch the invoker IAM check off
on the service (needs only `run.services.update`); if that is refused it
tries the classic allUsers binding (needs `run.services.setIamPolicy`); if
both are refused it prints the one command a project Owner runs.

**Where the votes are.** While hosted, `gs://<bucket>/votes.jsonl` is the
master copy and the committed `panel/votes.jsonl` is a snapshot: pull with
`--votes`, compare, commit. The correlation runs against either.

**Pointing the console at it.** In the Vercel project set
`VITE_PANEL_URL=https://<service>-<hash>-uc.a.run.app` (Production) and
redeploy the console; the value is baked in at build time.

## Storing votes in a Google Sheet (the no-server alternative)

If a server is ever unwanted, the vote store can instead be a Google Sheet
with a short Apps Script in front of it (`deploy/apps-script/Code.gs`): free,
under one account, no container. The console speaks to either; it picks the
transport from `VITE_PANEL_URL`. Kept as an option, not the deployment.

Once, about ten minutes:

```bash
.venv/bin/python -m runner.cli sheet-export        # writes key.csv (+ votes.csv from votes.jsonl)
```

1. Create a blank Google Sheet. Import `key.csv` into a tab named **`key`**
   (File → Import → Upload → Replace current sheet, then rename the tab).
   That tab is the blind; share the spreadsheet only with the study team.
2. Optional: import `votes.csv` into a tab named **`votes`** to carry over
   votes cast on the laptop server. The script creates the tab if absent.
3. Extensions → Apps Script, paste `Code.gs`, save. Deploy → New deployment
   → Web app → Execute as **Me**, Who has access **Anyone** → Deploy, copy
   the `/exec` URL. (After editing the script: Deploy → Manage → New version.)
4. In the studies console set `VITE_PANEL_URL` to that URL — Vercel project
   env for production, `apps/dashboard/.env.local` for a laptop — and rebuild.

What the script does: `POST` a vote as the reviewer saw it → resolve the
models from `key` → append `ts, reviewer, lane, run_id, scenario_id, picked,
over, reason`; `?action=votes&reviewer=` → that reviewer's last pick per
item as a media id (no model name); `?action=results` → every reviewer's
last vote per scenario, models named, for the hidden results page.

The correlation reads the sheet directly: File → Download → CSV of the
`votes` tab, then `correlate --votes votes.csv`.

## What is here

```
runner/runs.py        read a run folder into (scenario, two arms, inputs, brief, industry)
runner/voice_dashboard.py  voice from the committed voice/dashboard export
runner/strip.py       metadata stripping per format, no re-encode
runner/export.py      opaque ids, dist/ + private/key.json
runner/serve.py       stdlib server: static dist/ + POST /api/vote
runner/correlate.py   votes + scores.jsonl -> per-lane and overall report
runner/stats.py       Wilson, Spearman, Cohen's kappa in the stdlib
runner/cli.py         export | serve | correlate
site/index.html       the page (copied into dist/ on export)
tests/                offline; fake runs for all three lanes
```

Voice runs are not present on this machine (they live with the voice
session), so voice is imported from `voice/dashboard/` — the rendered report
and its 242 MP3s, which the ticket names as the voice source. One item per
scenario by default: the first (variant, pass) with a clip from both models,
preferring one where both were scored. The dashboard's score pills become a
synthetic `private/voice-dashboard/scores.jsonl` for the correlation; a
`0.0%` pill (a clip that failed its gates) is read as "no score". The
run-folder path for voice is still exercised by the tests.
