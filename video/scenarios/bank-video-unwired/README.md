# Unwired scenarios — buildable tasks, missing input assets

These are `image_to_video` / `video_edit` scenarios whose required input
asset does not exist on disk yet. Both tasks became buildable on
2026-09-09, and the loader now *enforces* that a buildable asset-fed task
has its asset — so leaving these in `bank-video-pending/` would reject any
run that loaded the directory.

They are parked here rather than deleted or stubbed: the scenarios are
real bank rows, and a stub asset would silently produce a comparison of
the wrong thing.

**Nothing here is generated automatically.** Producing these assets costs
money (stills) or needs sourcing (clips), so it is a deliberate step.

| Scenario | Task | Missing role | Title | In the edits+ads scope? |
|---|---|---|---|---|
| VID-AD-05 | `image_to_video` | `reference` | Logo sting | **YES** |
| VID-AD-08 | `image_to_video` | `reference` | SKU line-up pan | **YES** |
| VID-EDIT-10 | `video_edit` | `source` | Revert a prior edit | **YES** |
| VID-I2V-01 | `image_to_video` | `reference` | Subtle animation of a still | no |
| VID-I2V-02 | `image_to_video` | `reference` | First frame continuation | no |
| VID-I2V-03 | `image_to_video` | `reference` | First and last frame bridge | no |
| VID-I2V-04 | `image_to_video` | `reference` | Reference character in a new scene | no |
| VID-I2V-06 | `image_to_video` | `reference` | Landscape parallax | no |
| VID-I2V-07 | `image_to_video` | `reference` | Two references composited | no |
| VID-I2V-08 | `image_to_video` | `reference` | Style plate plus motion brief | no |
| VID-I2V-09 | `image_to_video` | `reference` | Logo reveal from a still | no |
| VID-I2V-10 | `image_to_video` | `reference` | Three stills to a sequence | no |

The three marked YES are inside the scope agreed on 4 September, so the
in-scope runnable set is **17 of 20** until their assets exist.

To restore one: create the asset under `video/assets/bank/` with its JSON
provenance sidecar, add the `inputs:` key, and move the file back to
`bank-video-pending/`.
