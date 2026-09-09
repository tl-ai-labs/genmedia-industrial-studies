#!/usr/bin/env bash
# Export one model's arm from a past run, for transfer to another machine.
#
# Run folders are never committed (.gitignore: **/runs/*) — they are large,
# dated, immutable artefacts. This packages only what is needed to merge a past
# arm into a new run: the images, plus the provenance needed to VERIFY they are
# what they claim to be (scenario_set_hash, frozen input SHAs, applied_params).
#
#   ./image/tools/export-pro-arm.sh 2026-09-02_011110_image
#
# Writes runs/<run-id>-<model>.tar.gz  (~25 MB for a 46-scenario Pro arm).

set -euo pipefail

RUN_ID="${1:?usage: export-pro-arm.sh <run-id> [model-id]}"
MODEL="${2:-gemini-3-pro-image-vertex}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # the image/ module
RUN_DIR="$HERE/runs/$RUN_ID"
[ -d "$RUN_DIR" ] || { echo "no such run: $RUN_DIR" >&2; exit 1; }

OUT="$HERE/runs/${RUN_ID}-${MODEL}.tar.gz"
cd "$RUN_DIR"

n=$(find outputs -name "$MODEL.*" ! -name '*.json' | wc -l | tr -d ' ')
[ "$n" -gt 0 ] || { echo "no $MODEL outputs in $RUN_ID" >&2; exit 1; }

# images + the small files that prove provenance; no other model's outputs
LIST=$(mktemp)
trap 'rm -f "$LIST"' EXIT
find outputs -name "$MODEL.*" >> "$LIST"
for f in manifest.json telemetry.jsonl checks.jsonl scores.jsonl; do
  [ -f "$f" ] && echo "$f" >> "$LIST"
done
[ -d scenarios ] && find scenarios -type f >> "$LIST"

tar -czf "$OUT" -T "$LIST"

echo "exported $n ${MODEL} image(s) + provenance"
echo "  $OUT  ($(du -h "$OUT" | cut -f1))"
echo
echo "Send that file over. On the receiving machine, from the image/ module:"
echo "  mkdir -p runs/$RUN_ID && tar -xzf ${RUN_ID}-${MODEL}.tar.gz -C runs/$RUN_ID"
