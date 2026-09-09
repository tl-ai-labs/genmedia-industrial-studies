# Unwired scenarios — buildable task, no input asset

A scenario lands here when its task is buildable but its required input does
not exist on disk. The loader rejects such a scenario, so leaving it in
`bank-video-pending/` would reject any run that loaded the directory.

Parked, not stubbed: a placeholder asset would silently produce a comparison
of the wrong thing, which is worse than a scenario that cannot run.

| Scenario | Task | Needs | In the edits+ads scope? |
|---|---|---|---|
| VID-EDIT-10 | `video_edit` | a `source` clip | **yes** |

**VID-EDIT-10 "Derived source"** declares no `inputs` at all — the sheet
describes its source as produced at run time from another scenario's output,
which the runner has no mechanism for. It needs either a real source clip on
disk or a decision to retire it.

Eleven scenarios were parked here on 2026-09-09 and released again the same
day: they had their assets all along, under role names that carry meaning
(`first_frame`/`last_frame`, `character`/`environment`, `still1..3`, `logo`,
`style`). The task definition had wrongly demanded a single role called
`reference`; it now requires a COUNT of inputs, not a name. See
`runner/lifecycle.py`.

To restore VID-EDIT-10: put the clip at `assets/bank/` with its JSON
provenance sidecar (source clips are never model-generated), add the
`inputs: {source: ...}` key, and move the file to `bank-video-pending/`.
