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
.venv/bin/python -m pytest          # 37 tests, no keys, no network

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
right, pick}`; the **server** resolves the models from the key and writes the
full record. Nothing in devtools, a hover, or a saved file names the model.

What is *not* hidden, and cannot be: the content. A reviewer who knows the
models' house styles can guess. That is the same limit the LLM judge's
blinding has, and it is why the correlation is reported with its n.

## What a vote looks like

`votes.jsonl`, one record per vote, appended by the server:

```json
{"ts": "2026-09-10T09:14:02.118Z", "lane": "image", "run_id": "2026-09-01_224335_image",
 "scenario_id": "IMG-TXT-01", "item": "3f9a0c11d2", "reviewer": "sai",
 "pick": "right", "picked_model": "gpt-image-2-high",
 "left_model": "gemini-3-pro-image-vertex", "right_model": "gpt-image-2-high"}
```

The page offers no "can't tell" (a forced choice, at the study lead's request
on 10 Sep); the server and the correlation still accept `pick: "tie"` with
`picked_model: null` should that be reinstated. The file stands on its own — the
correlation never needs the key — and it is small enough to commit, which is
why it is not gitignored while `dist/` and `private/` are. A reviewer who
revisits an item and votes again appends a second record; the correlation
keeps the **last** vote per (reviewer, item).

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
  can reach (LAN or a tunnel), send the URL. Reviewers type a name once;
  the page keeps their place and hides items they have already voted on.
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
