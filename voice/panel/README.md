# voice/panel — blind pairwise listener panel

A web page for collecting **human A/B preference** on the voice clips, blind:
the reviewer sees the line that was spoken and two versions, **A** and **B**,
and says which they prefer. They are never told which system made which clip.

This is the "blind human listener panel" from `voice/HANDOFF.md` §10 — a
pairwise cross-check on the LLM judge, which is pointwise and shares a vendor
with one arm (§9).

## What it compares

`elevenlabs-v3` vs `gemini-3-1-flash-tts`, take **p1**, across the 29 scenario
cards that have a clean pair in `voice/dashboard/audio/` — every `voice-p1`
clip in the current v3 run, including the `vr-ecom-06` and `vr-game-04`
variants. Plus a couple of identical-pair integrity cards.

## Files

```
index.html            the page. Vanilla JS, no build step. Opens by
                      double-click; falls back to serve.py.
manifest.blind.js     what the page loads: per card the script, task, language,
                      target style, and the clip pair — as opaque ids, no
                      model names. window.PANEL = {...}.
clips/c_*.mp3         the audio, copied byte-for-byte from ../dashboard/audio/,
                      renamed to salted-hash ids so nothing leaks the model.
build.py              regenerates manifest.blind.js + clips/ + reveal.json
                      + panel-for-review.zip.
tally.py              de-blinds the returned votes and reports the result.
serve.py              tiny static server, for browsers that block file:// clips.
vercel.json           SPA rewrite, if this is ever hosted at a URL.

reveal.json           LOCAL ONLY (gitignored, .vercelignore). clip id -> model.
                      The de-blind key. Never hand this to a reviewer.
exports/              drop the JSON files reviewers send back here; tally.py
                      reads the folder.
panel-for-review.zip  LOCAL ONLY. index.html + manifest.blind.js + clips/ +
                      serve.py + HOW-TO.txt. This is what you send out — it
                      does not contain reveal.json.
```

Same seam as `voice/dashboard/`: the page and the audio it plays are
committed; the raw run evidence and the answer key are not.

## Rebuild

```bash
python voice/panel/build.py
```

Offline, no API key. Deterministic — the opaque names are `sha1(card|model|salt)`,
so re-running rewrites a clip only if the source clip actually changed.

Options: `--run <id>` to read loudness from a specific `voice/runs/` folder
(default: latest `*_voice-p1`), `--attention-checks N` (default 2, `0` off),
`--no-zip`.

## Hand it to reviewers

Send `panel-for-review.zip`. They unzip, open `index.html` (or run
`python serve.py`), type their initials, work through the cards, and on the
last screen click **Copy JSON** / **Download**. They send the JSON back.

Progress is saved in the reviewer's browser (`localStorage`, keyed by name +
manifest hash), so they can stop and resume on the same machine.

## Tally

```bash
# put the returned files in exports/ first
python voice/panel/tally.py
```

Reports preference per model pooled over reviewers, a two-sided sign test over
the decided votes, the per-repo verdict rule (a side needs ≥70% of decided
votes and ≥10 decided), per-card leaning, inter-reviewer agreement, and which
reviewers failed the identical-pair checks. Writes `tally.md`.

## Loudness

The clips are **not** modified. Within each pair the louder clip is attenuated
in the browser (`<audio>.volume ≤ 1`) down to the level of its quieter
partner, computed from `rms_dbfs` in the run's `checks.jsonl`. So the two
clips a reviewer compares are level-matched without touching a file. Level is
matched **within** a pair, not across cards — set a comfortable volume on the
first clip of each card.

## Limits

- One take (`p1`) per arm. Run-to-run spread is the dashboard's job, not this
  page's; if a card lands close in the tally, re-run just that one with `p2`.
- ElevenLabs v3 is sent plain text — its inline audio tags (`[whispers]`) are
  not exercised, same as the rest of the study.
- Preference here is holistic ("which do you like"), not a rubric score. It
  informs the verdict; it does not replace the scored criteria.
