#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
INGRESS_SERVICE="${SEARCHARIS_INGRESS_SERVICE:-searcharis-ingress}"
WORKER_SERVICE="${SEARCHARIS_WORKER_SERVICE:-searcharis-worker}"
DEMO_SERVICE="${SEARCHARIS_DEMO_SERVICE:-searcharis-demo-target}"
TOPIC="${SEARCHARIS_PUBSUB_TOPIC:-searcharis-deployments}"
REPOSITORY="${SEARCHARIS_DEMO_REPOSITORY:?SEARCHARIS_DEMO_REPOSITORY is required}"

INGRESS_URL="$(gcloud run services describe "${INGRESS_SERVICE}" --region="${REGION}" --format='value(status.url)')"
WORKER_URL="$(gcloud run services describe "${WORKER_SERVICE}" --region="${REGION}" --format='value(status.url)')"
DEMO_URL="$(gcloud run services describe "${DEMO_SERVICE}" --region="${REGION}" --format='value(status.url)')"

echo "Smoke ingress readiness: ${INGRESS_URL}/ready"
curl --fail --silent --show-error "${INGRESS_URL}/ready" >/dev/null
echo "Smoke ingress readiness: ok"

echo "Smoke worker readiness: ${WORKER_URL}/ready"
WORKER_ID_TOKEN="${SEARCHARIS_WORKER_ID_TOKEN:-}"
if [[ -z "${WORKER_ID_TOKEN}" ]]; then
  WORKER_ID_TOKEN="$(gcloud auth print-identity-token)"
fi
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${WORKER_ID_TOKEN}" \
  "${WORKER_URL}/ready" >/dev/null
echo "Smoke worker readiness: ok"
unset WORKER_ID_TOKEN

echo "Smoke demo target: ${DEMO_URL}/"
HTML="$(curl --fail --silent --show-error "${DEMO_URL}/")"
if [[ "${HTML}" != *"<title>"* ]]; then
  echo "Demo target is not currently healthy; expected a title." >&2
  exit 1
fi
echo "Smoke demo target: ok"

EVENT_ID="smoke-$(date -u +%Y%m%dT%H%M%SZ)"
PAYLOAD="$(printf '{\"event_id\":\"%s\",\"repository\":\"%s\",\"target_url\":\"%s\",\"commit_sha\":\"smoke000\",\"source\":\"demo\"}' "${EVENT_ID}" "${REPOSITORY}" "${DEMO_URL}")"
echo "Smoke Pub/Sub publish: ${TOPIC} (${EVENT_ID})"
gcloud pubsub topics publish "${TOPIC}" --message="${PAYLOAD}" >/dev/null
echo "Smoke Pub/Sub publish: ok"

sleep 2
echo "Smoke incident API: ${INGRESS_URL}/api/incidents"
curl --fail --silent --show-error "${INGRESS_URL}/api/incidents" >/dev/null
echo "Smoke incident API: ok"

echo "Searcharis smoke checks passed."
