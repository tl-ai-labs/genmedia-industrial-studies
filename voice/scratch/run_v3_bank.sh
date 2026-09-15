#!/usr/bin/env bash
# v3 bank re-run — one whole-bank pass. Usage: bash scratch/run_v3_bank.sh p1
# Identical for p1 and p2 except the --label. p1 and p2 are the two passes
# for the noise floor.
#
# --bundle: ONE run folder with every scenario+variant exactly once. Per-
# scenario mode (the default for a directory) re-loads a variant file once
# per variant, so vr-game-04's 6 NPC variants would each regenerate all six
# — 6x the clips and quota. --bundle avoids that and makes p1/p2 two clean
# whole-bank runs to diff.
#
# TOKEN FRESHNESS GUARD. `gcloud auth print-access-token` will hand back a
# CACHED near-expiry token (it only auto-refreshes within ~5 min of expiry),
# which on 2026-09-09 died 11 min into a pass -> 401 ACCESS_TOKEN_TYPE_
# UNSUPPORTED on every later Gemini call. Fix: drop the access-token cache to
# force a fresh mint from the (still valid) refresh token, then assert >=3000s
# of life before spending.
set -euo pipefail
LABEL="${1:?pass label, e.g. p1}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."

rm -f "$HOME/.config/gcloud/access_tokens.db"   # force a freshly-minted access token
TOKEN="$(gcloud auth print-access-token)"
EXP="$(curl -s "https://oauth2.googleapis.com/tokeninfo?access_token=${TOKEN}" \
        | python3 -c 'import sys,json; print(json.load(sys.stdin).get("expires_in",0))')"
echo "Vertex token minted: ${EXP}s of life"
if [ "${EXP:-0}" -lt 3000 ]; then
  echo "ABORT: token only ${EXP}s — too short for a full pass. Run: gcloud auth login" >&2
  exit 1
fi

GOOGLE_OAUTH_ACCESS_TOKEN="$TOKEN" \
GOOGLE_APPLICATION_CREDENTIALS="oauth-token-workaround" \
GCP_PROJECT_ID="ai-studies-console" \
.venv/bin/python -m runner.cli --modality voice \
  --scenarios scenarios/ \
  all --label "$LABEL" --yes --bundle \
  --budget 3.00 --judge-budget 2.00 --workers 4 --timeout 90
