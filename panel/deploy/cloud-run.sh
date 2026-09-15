#!/usr/bin/env bash
# Host the blind panel on Cloud Run with its votes in a Cloud Storage bucket.
#
#   deploy/cloud-run.sh            first time and after a code change: build + deploy + sync
#   deploy/cloud-run.sh --sync     after a re-export: copy items.json and the key up, no rebuild
#   deploy/cloud-run.sh --votes    pull the hosted votes.jsonl down next to this script's repo copy
#   deploy/cloud-run.sh --url      print the service URL (what VITE_PANEL_URL must be)
#
# WHY THIS SHAPE (2026-09-11, Sai: "gcloud is best, we already have the
# billing"). us-central1 is where Google's always-free tier lives: 5 GB of
# regional Cloud Storage, 2 M Cloud Run requests and 180 k vCPU-seconds a
# month - a panel with a handful of reviewers sits well inside all of it.
# The bucket holds what changes (items.json, key, votes) - NOT the media: the
# pairs' files are served by the studies console itself, copied verbatim
# under its public/reports/genmedia-blind-panel/media/, and the server tells
# its own page where they are via PANEL_MEDIA_BASE. The image holds code, so
# it stays inside Artifact Registry's free 0.5 GB. ONE instance at most: a
# vote is an append to one file on a FUSE mount, and two instances appending
# to the same object would overwrite each other's lines.
#
# PUBLIC ACCESS. Cloud Run answers only authenticated callers unless told
# otherwise. The classic way (`--allow-unauthenticated`, an IAM binding for
# allUsers) needs run.services.setIamPolicy, which a project Editor does not
# have; the newer way (`--no-invoker-iam-check`) switches the check off on
# the service and needs only run.services.update. Both are attempted; the
# script reports which one took.
set -euo pipefail

PROJECT="${PANEL_GCP_PROJECT:-ai-studies-console}"
REGION="${PANEL_REGION:-us-central1}"
SERVICE="${PANEL_SERVICE:-genmedia-panel}"
BUCKET="${PANEL_BUCKET:-genmedia-panel-${PROJECT}}"
REPO="${PANEL_AR_REPO:-genmedia-panel}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${SERVICE}"
# Where the media is served from - the console's copy of dist/media.
MEDIA_BASE="${PANEL_MEDIA_BASE:-https://studies-dev.adlc.tilicho.in/reports/genmedia-blind-panel/media}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # panel/
cd "$HERE"

mode="${1:-all}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

url() { gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(status.url)' 2>/dev/null || true; }

sync_data() {
  say "Syncing items.json, the page and the private key to gs://${BUCKET} (no media; votes are never touched)"
  test -f dist/items.json || { echo "dist/items.json missing - run 'python -m runner.cli export' first" >&2; exit 1; }
  test -f private/key.json || { echo "private/key.json missing - run the export first" >&2; exit 1; }
  gcloud storage cp dist/items.json dist/index.html "gs://${BUCKET}/dist/" --project "$PROJECT"
  gcloud storage cp private/key.json "gs://${BUCKET}/private/key.json" --project "$PROJECT"
  # Seed an empty votes file once so the server's first read has something to open.
  if ! gcloud storage ls "gs://${BUCKET}/votes.jsonl" --project "$PROJECT" >/dev/null 2>&1; then
    : > /tmp/votes.empty && gcloud storage cp /tmp/votes.empty "gs://${BUCKET}/votes.jsonl" --project "$PROJECT"
  fi
  echo "Media is served by the console from ${MEDIA_BASE} - copy dist/media there (ai-studies-console: apps/dashboard/public/reports/genmedia-blind-panel/media) and commit."
}

case "$mode" in
  --url) url; exit 0 ;;
  --votes)
    say "Pulling gs://${BUCKET}/votes.jsonl -> panel/votes.hosted.jsonl (compare, then commit as votes.jsonl when you are ready)"
    gcloud storage cp "gs://${BUCKET}/votes.jsonl" votes.hosted.jsonl --project "$PROJECT"
    wc -l votes.hosted.jsonl; exit 0 ;;
  --sync) sync_data; exit 0 ;;
  all) ;;
  *) echo "usage: $0 [--sync|--votes|--url]" >&2; exit 2 ;;
esac

say "Project ${PROJECT}, region ${REGION}, service ${SERVICE}, bucket gs://${BUCKET}"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com storage.googleapis.com --project "$PROJECT"

if ! gcloud storage buckets describe "gs://${BUCKET}" --project "$PROJECT" >/dev/null 2>&1; then
  say "Creating the bucket (regional, uniform access, private)"
  gcloud storage buckets create "gs://${BUCKET}" --project "$PROJECT" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention
fi
sync_data

# The service runs as the project's default compute service account; the
# bucket mount needs it to read the key and to rewrite votes.jsonl.
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
RUN_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
say "Letting ${RUN_SA} read and write the bucket"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" --project "$PROJECT" \
  --member "serviceAccount:${RUN_SA}" --role roles/storage.objectAdmin >/dev/null

if ! gcloud artifacts repositories describe "$REPO" --project "$PROJECT" --location "$REGION" >/dev/null 2>&1; then
  say "Creating the Artifact Registry repo"
  gcloud artifacts repositories create "$REPO" --project "$PROJECT" --location "$REGION" --repository-format docker
fi

say "Building the image with Cloud Build (code only, ~50 MB)"
gcloud builds submit --project "$PROJECT" --region "$REGION" --tag "${IMAGE}:latest" .

say "Deploying to Cloud Run with the bucket mounted at /data"
gcloud run deploy "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --image "${IMAGE}:latest" \
  --min-instances 0 --max-instances 1 --concurrency 80 \
  --cpu 1 --memory 512Mi --timeout 60 \
  --add-volume "name=data,type=cloud-storage,bucket=${BUCKET}" \
  --add-volume-mount "volume=data,mount-path=/data" \
  --set-env-vars "PANEL_DATA=/data,PANEL_MEDIA_BASE=${MEDIA_BASE}"

say "Opening the service to the public"
if gcloud run services update "$SERVICE" --project "$PROJECT" --region "$REGION" --no-invoker-iam-check >/dev/null 2>&1; then
  echo "invoker IAM check disabled (run.services.update was enough)"
elif gcloud run services add-iam-policy-binding "$SERVICE" --project "$PROJECT" --region "$REGION" \
       --member=allUsers --role=roles/run.invoker >/dev/null 2>&1; then
  echo "allUsers granted roles/run.invoker"
else
  echo "NOT PUBLIC: neither way was permitted for this account. A project Owner runs:"
  echo "  gcloud run services add-iam-policy-binding $SERVICE --project $PROJECT --region $REGION --member=allUsers --role=roles/run.invoker"
fi

say "Live at: $(url)"
echo "Health (unauthenticated): $(curl -s -o /dev/null -w '%{http_code}' "$(url)/api/health")"
echo
echo "Point the studies console at it: set VITE_PANEL_URL=$(url) in the Vercel project (Production) and redeploy the console."
