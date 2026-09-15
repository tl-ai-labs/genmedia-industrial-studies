# voice/review — what people heard, kept apart from what code measured

One hand-written file, `human-review.yaml`, read by both boards on every
render. It holds two kinds of record and the page keeps them visibly separate:

| Record | What it is | What it does to the numbers |
| --- | --- | --- |
| **observation** | What a listener heard on a scenario, case by case: notes, an optional preference, tags. | **Nothing.** Rendered in a dashed *Human review* block on the card and in its own section/tab. Never enters a score, a gate rate, a spread or a winner. |
| **correction** | An automated result a listener found **wrong**, and the right result. | Applied to the loaded cells before rendering, so the automated numbers reflect the corrected result. Every corrected clip is stamped *corrected* with what the instrument originally said, who corrected it, when and why. The full list is printed on the page. |

The run folders are never edited. `--no-review` renders the instrument's own
answer untouched; the difference between the two renders is exactly the list
of corrections.

```bash
cd voice
.venv/bin/python -m runner.cli --modality voice client-report --out dashboard   # reads review/ by default
.venv/bin/python -m runner.cli --modality voice dashboard --no-review           # the instrument, untouched
.venv/bin/python -m runner.cli --modality voice dashboard --review path/to/other.yaml
```

## The file

```yaml
reviewers:                       # optional; if present, every record must name one of these
  - id: sai
    name: Sai Nadh
    role: voice lead

corrections:
  - scenario_id: vr-ecom-06#nato # EXACT clip id - a variant is parent#variant
    model_id: gemini-3-1-flash-tts
    run_label: voice-p1          # the run's own label; omit to hit every run of that clip
    field: gate                  # gate | wer | score
    gate: must_say_digits        # gate corrections name the gate
    was: false                   # optional guard: refused if the run recorded otherwise
    now: true
    reason: >-                   # required, and it goes on the page
      The clip reads "four one nine" cleanly; the transcript wrote "for one nine",
      which is a transcriber error, not a model error.
    reviewer: sai
    date: 2026-09-14

observations:
  - scenario_id: vr-ads-06       # the card; a variant id is fine too
    reviewer: sai
    date: 2026-09-14
    model_id: elevenlabs-v3      # omit for a note about both models
    run_label: voice-p2          # optional
    preference: gemini-3-1-flash-tts   # optional: a model id, "tie" or "cant_tell"
    tags: [pace, pronunciation]  # optional
    notes: >-
      Reads the disclaimer at a natural broadcast clip but swallows "subject to"
      both times. Gemini is faster and every word lands.
```

## What a correction may touch

Only things the instrument **measured** and got wrong:

- `gate` — a gate verdict. `now: true` clears it; if every gate now passes and
  the clip had been gated, it becomes *cleared on review, not scored* — the
  judge never heard a gated clip, and nobody here invents its score. Re-run
  `judge` on that run to score it. `now: false` fails it, which invalidates
  the clip exactly as it would have at run time.
- `wer` — the word error rate, when the transcript rather than the clip was
  wrong.
- `score` — the judge's weighted score, when it rested on a wrong measured
  fact (the judge is told the measurements as established truth).

A correction cannot give a score to a clip the judge did not score. That would
be a human score wearing the judge's badge — write an observation.

## Mistakes are refused, never skipped

A correction that names a clip, a gate, a run or a reviewer that does not
exist stops the render with a message saying which. A `was` that does not
match the run does the same: the correction was written against a different
run. A silently ignored correction would leave a wrong number standing on a
client-facing page while this file says it was fixed.

## Where to find ids

- Scenario ids and variants: the card headings on the page, or
  `ls dashboard/audio/` — clips are named `<scenario>--<model>--[<variant>--]<run_label>.mp3`.
- Gate names: the internal board shows every gate on every clip; or
  `checks.jsonl` in the run folder.
- Run labels: `voice-p1`, `voice-p2` for the two passes of the v3 re-run.
