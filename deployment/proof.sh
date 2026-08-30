#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
INGRESS_SERVICE="${SEARCHARIS_INGRESS_SERVICE:-searcharis-ingress}"
DEMO_SERVICE="${SEARCHARIS_DEMO_SERVICE:-searcharis-demo-target}"
TOPIC="${SEARCHARIS_PUBSUB_TOPIC:-searcharis-deployments}"
REPOSITORY="${SEARCHARIS_DEMO_REPOSITORY:?SEARCHARIS_DEMO_REPOSITORY is required}"
DUPLICATES="${SEARCHARIS_PROOF_DUPLICATES:-5}"
OPEN_TIMEOUT="${SEARCHARIS_PROOF_OPEN_TIMEOUT:-180}"
RESOLVE_TIMEOUT="${SEARCHARIS_PROOF_RESOLVE_TIMEOUT:-240}"

if ! [[ "${DUPLICATES}" =~ ^[1-9][0-9]*$ ]] || (( DUPLICATES > 10 )); then
  echo "SEARCHARIS_PROOF_DUPLICATES must be between 1 and 10" >&2
  exit 2
fi

INGRESS_URL="$(gcloud run services describe "${INGRESS_SERVICE}" --region="${REGION}" --format='value(status.url)')"
DEMO_URL="$(gcloud run services describe "${DEMO_SERVICE}" --region="${REGION}" --format='value(status.url)')"
EVENT_ID="proof-${GITHUB_RUN_ID:-manual}-${GITHUB_RUN_ATTEMPT:-1}-$(date -u +%Y%m%dT%H%M%SZ)"
COMMIT_SHA="${GITHUB_SHA:-proof000000000000000000000000000000000000}"
COMMIT_SHA="${COMMIT_SHA:0:12}"

restore_healthy() {
  echo "Restoring healthy demo traffic..."
  gcloud run services update-traffic "${DEMO_SERVICE}" \
    --region="${REGION}" \
    --to-tags=healthy=100 \
    --quiet >/dev/null || true
}
trap restore_healthy EXIT

BASELINE_IDS="$(curl --fail --silent --show-error "${INGRESS_URL}/api/incidents" | jq -c '[.[].incident_id]')"
echo "Baseline incidents: $(jq 'length' <<<"${BASELINE_IDS}")"

echo "Switching demo target to broken revision..."
gcloud run services update-traffic "${DEMO_SERVICE}" \
  --region="${REGION}" \
  --to-tags=broken=100 \
  --quiet >/dev/null

HTML="$(curl --fail --silent --show-error "${DEMO_URL}/")"
if [[ "${HTML}" == *"<title>"* ]]; then
  echo "Broken revision still contains a title; refusing to run proof." >&2
  exit 1
fi
echo "Broken target confirmed: <title> is absent."

PAYLOAD="$(jq -cn \
  --arg event_id "${EVENT_ID}" \
  --arg repository "${REPOSITORY}" \
  --arg target_url "${DEMO_URL}" \
  --arg commit_sha "${COMMIT_SHA}" \
  '{event_id:$event_id,repository:$repository,target_url:$target_url,commit_sha:$commit_sha,source:"demo"}')"

echo "Publishing ${DUPLICATES} identical events with event_id=${EVENT_ID}..."
seq 1 "${DUPLICATES}" | xargs -P "${DUPLICATES}" -I{} \
  gcloud pubsub topics publish "${TOPIC}" --message="${PAYLOAD}" >/dev/null
echo "Duplicate publish burst complete."

INCIDENT_ID=""
ISSUE_NUMBER=""
ISSUE_URL=""
OPEN_DEADLINE=$(( $(date +%s) + OPEN_TIMEOUT ))
while (( $(date +%s) < OPEN_DEADLINE )); do
  INCIDENTS="$(curl --fail --silent --show-error "${INGRESS_URL}/api/incidents")"
  NEW_INCIDENTS="$(jq -c --argjson baseline "${BASELINE_IDS}" \
    '[.[] | select(.incident_id as $id | ($baseline | index($id)) == null)]' <<<"${INCIDENTS}")"
  NEW_COUNT="$(jq 'length' <<<"${NEW_INCIDENTS}")"
  if (( NEW_COUNT > 1 )); then
    echo "Idempotency failure: ${NEW_COUNT} new incidents appeared for one event identity." >&2
    jq . <<<"${NEW_INCIDENTS}" >&2
    exit 1
  fi
  if (( NEW_COUNT == 1 )); then
    INCIDENT_ID="$(jq -r '.[0].incident_id' <<<"${NEW_INCIDENTS}")"
    ISSUE_NUMBER="$(jq -r '.[0].github_issue_number // empty' <<<"${NEW_INCIDENTS}")"
    ISSUE_URL="$(jq -r '.[0].github_issue_url // empty' <<<"${NEW_INCIDENTS}")"
    STATE="$(jq -r '.[0].state' <<<"${NEW_INCIDENTS}")"
    echo "Observed incident ${INCIDENT_ID}: state=${STATE}, issue=${ISSUE_NUMBER:-pending}"
    if [[ -n "${ISSUE_NUMBER}" ]]; then
      break
    fi
  fi
  sleep 3
done

if [[ -z "${INCIDENT_ID}" || -z "${ISSUE_NUMBER}" ]]; then
  echo "Timed out waiting for one GitHub-backed incident." >&2
  exit 1
fi

# Recovery should be observable before the scheduled verification runs.
restore_healthy
trap - EXIT
sleep 2
HTML="$(curl --fail --silent --show-error "${DEMO_URL}/")"
if [[ "${HTML}" != *"<title>"* ]]; then
  echo "Healthy revision did not restore the title." >&2
  exit 1
fi
echo "Healthy target confirmed: <title> restored."

RESOLVE_DEADLINE=$(( $(date +%s) + RESOLVE_TIMEOUT ))
FINAL_STATE=""
while (( $(date +%s) < RESOLVE_DEADLINE )); do
  INCIDENTS="$(curl --fail --silent --show-error "${INGRESS_URL}/api/incidents")"
  FINAL_STATE="$(jq -r --arg id "${INCIDENT_ID}" '.[] | select(.incident_id == $id) | .state' <<<"${INCIDENTS}")"
  echo "Waiting for verification: incident=${INCIDENT_ID}, state=${FINAL_STATE:-missing}"
  if [[ "${FINAL_STATE}" == "RESOLVED" ]]; then
    break
  fi
  if [[ "${FINAL_STATE}" == "FAILED_TERMINAL" ]]; then
    echo "Verification reached FAILED_TERMINAL." >&2
    exit 1
  fi
  sleep 5
done

if [[ "${FINAL_STATE}" != "RESOLVED" ]]; then
  echo "Timed out waiting for RESOLVED; final state=${FINAL_STATE:-missing}." >&2
  exit 1
fi

ISSUE_JSON="$(curl --fail --silent --show-error \
  -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/repos/${REPOSITORY}/issues/${ISSUE_NUMBER}")"
if [[ "$(jq -r '.state' <<<"${ISSUE_JSON}")" != "closed" ]]; then
  echo "GitHub issue ${ISSUE_NUMBER} was not closed after verified recovery." >&2
  exit 1
fi
if jq -e 'has("pull_request")' <<<"${ISSUE_JSON}" >/dev/null; then
  echo "Expected an issue, received a pull request object." >&2
  exit 1
fi

FINAL_INCIDENTS="$(curl --fail --silent --show-error "${INGRESS_URL}/api/incidents")"
NEW_FINAL="$(jq -c --argjson baseline "${BASELINE_IDS}" \
  '[.[] | select(.incident_id as $id | ($baseline | index($id)) == null)]' <<<"${FINAL_INCIDENTS}")"
if [[ "$(jq 'length' <<<"${NEW_FINAL}")" != "1" ]]; then
  echo "Final idempotency assertion failed: expected exactly one new incident." >&2
  jq . <<<"${NEW_FINAL}" >&2
  exit 1
fi

echo "Searcharis live proof passed."
echo "Event ID: ${EVENT_ID}"
echo "Duplicate publishes: ${DUPLICATES}"
echo "Incident ID: ${INCIDENT_ID}"
echo "GitHub issue: ${ISSUE_URL}"
echo "Final state: ${FINAL_STATE}"
