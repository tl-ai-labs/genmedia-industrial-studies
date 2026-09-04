# voice/dashboard — the shareable report, committed

**Everything in this folder is generated. Do not edit it by hand.**
The next export overwrites it, and a hand-edit would put a number on a
client-facing page that no run produced.

## Why it is committed when `runs/` is not

`voice/runs/` is the raw evidence — 44 runs, 288 MB, every clip and every
measurement — and it is gitignored (`**/runs/*`). This folder is the
**export**: the finished report and only the audio it plays, so the thing we
actually send people travels with the repo and can be deployed from it.

Same seam as `apps/dashboard/public/data` in the study console: raw evidence
stays out of git, the rendered product goes in.

## Regenerating

```bash
cd voice
.venv/bin/python -m runner.cli --modality voice client-report --out dashboard
```

Free, offline, no API key. It reads `voice/runs/` and rewrites this folder.

The MP3 encoder is deterministic, so re-exporting after an unrelated change
rewrites nothing — only clips whose source actually changed produce a diff.
That is what keeps 16 MB of audio from churning into git history on every
rebuild.

## What is here

```
index.html    the report — 184 KB, opens in about 100 ms
audio/        242 clips, one MP3 per clip, ~16.5 MB
vercel.json   SPA rewrite, so this folder can deploy as-is to a URL
```

Open it locally with `open voice/dashboard/index.html`, or send the whole
folder. The page reads `audio/` beside it, so the folder must stay together.

## If you need one file instead

```bash
.venv/bin/python -m runner.cli --modality voice client-report --inline
```

Writes a single self-contained 22 MB `runs/client-report.html` with every
clip embedded — the right shape when it has to travel as one attachment.
That one is **not** committed; it is reproducible in seconds and 22 MB of
base64 does not belong in git.

## What this report is

A comparison of `gemini-3-1-flash-tts` and `elevenlabs-multilingual-v2`
across 17 real industry scenarios. It is built for an outside audience and
states its own limits on the page: the judge is a Google model, the judge is
uncalibrated, and four of the five decided scenarios have a gap smaller than
the noise the model shows against itself.

Full context: `voice/HANDOFF.md`.
