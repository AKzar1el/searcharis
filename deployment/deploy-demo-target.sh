#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE="${SEARCHARIS_DEMO_SERVICE:-searcharis-demo-target}"

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "GOOGLE_CLOUD_PROJECT is required" >&2
  exit 2
fi

gcloud run deploy "${SERVICE}" \
  --source demo_target \
  --region="${REGION}" \
  --allow-unauthenticated \
  --set-env-vars="TARGET_VARIANT=healthy" \
  --tag=healthy \
  --quiet

DEMO_URL="$(gcloud run services describe "${SERVICE}" --region="${REGION}" --format='value(status.url)')"

gcloud run deploy "${SERVICE}" \
  --source demo_target \
  --region="${REGION}" \
  --allow-unauthenticated \
  --set-env-vars="TARGET_VARIANT=broken" \
  --no-traffic \
  --tag=broken \
  --quiet

gcloud run services update-traffic "${SERVICE}" \
  --region="${REGION}" \
  --to-tags=healthy=100 \
  --quiet

cat <<OUT
Demo target: ${DEMO_URL}
Healthy tag receives 100% traffic.

Video traffic switches:
  gcloud run services update-traffic ${SERVICE} --region=${REGION} --to-tags=broken=100
  gcloud run services update-traffic ${SERVICE} --region=${REGION} --to-tags=healthy=100
OUT
