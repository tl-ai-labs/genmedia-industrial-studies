# VID-AD-06 re-run — 2026-09-10

Run 2026-09-10_145714_video marked this scenario `invalid` on BOTH arms.
Neither model was at fault: both delivered exactly 1080x1920, the 9:16
vertical the brief asks for. The scenario's own gate demanded
`min_width: 1280` — a landscape floor no portrait clip can clear.

Runs are immutable, so the correction is a new run rather than a re-check of
the old one. The gate is fixed here and in the bank: min_width 720,
min_height 1280.
