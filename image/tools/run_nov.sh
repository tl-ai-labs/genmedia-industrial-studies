#!/bin/sh
# November run: generate, judge (Gemini 3.1 Pro) and build the run report, in one go.
#   tools/run_nov.sh test    5 scenarios, under $2, caps $2 / OpenAI $1
#   tools/run_nov.sh main    100 scenarios, about $21, caps $25 / OpenAI $12
#   tools/run_nov.sh resume <run-id> [main|test]   carry on a stopped run for free
# Keeps the Mac awake while it runs; the lid must stay open and on power.
set -eu
cd "$(dirname "$0")/.."
PY="${PY:-/Users/macbook/Documents/GitHub/Google-Projects/genmedia-eval/.venv/bin/python}"
MODELS=configs/models-nb21-vs-gpt25-judge31pro.yaml
case "${1:-}" in
  test) LIST=scenarios/tiers/nov-test.txt; CAP=2; OPENAI=1; RUN="" ;;
  main) LIST=scenarios/tiers/nov-100.txt; CAP=25; OPENAI=12; RUN="" ;;
  resume) RUN="--run ${2:?run id}"; if [ "${3:-main}" = test ]; then LIST=scenarios/tiers/nov-test.txt; CAP=2; OPENAI=1; else LIST=scenarios/tiers/nov-100.txt; CAP=25; OPENAI=12; fi ;;
  *) echo "usage: tools/run_nov.sh test | main | resume <run-id> [main|test]"; exit 2 ;;
esac
LOG=$(mktemp)
caffeinate -ims "$PY" -m runner.cli run --modality image --scenarios "$LIST" --models "$MODELS" \
  --budget "$CAP" --budget-provider "openai=$OPENAI" $RUN | tee "$LOG"
ID=$(sed -n 's#^run folder: .*/runs/##p' "$LOG" | tail -1)
[ -n "$ID" ] || { echo "no run folder; stopping before judging"; exit 1; }
caffeinate -ims "$PY" -m runner.cli judge --run "$ID" --models "$MODELS"
caffeinate -ims "$PY" -m runner.cli judge --run "$ID" --models "$MODELS" --retry-unjudged
"$PY" -m runner.cli report --run "$ID"
echo "done: runs/$ID"
