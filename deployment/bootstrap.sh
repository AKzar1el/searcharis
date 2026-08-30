#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
TOPIC="${SEARCHARIS_PUBSUB_TOPIC:-searcharis-deployments}"
QUEUE="${SEARCHARIS_TASKS_QUEUE:-searcharis-verification}"
DATABASE_ID="${SEARCHARIS_FIRESTORE_DATABASE:-(default)}"

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "GOOGLE_CLOUD_PROJECT is required" >&2
  exit 2
fi

gcloud config set project "${PROJECT_ID}" >/dev/null

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  pubsub.googleapis.com \
  firestore.googleapis.com \
  cloudtasks.googleapis.com \
  secretmanager.googleapis.com

create_service_account() {
  local name="$1"
  local display="$2"
  if ! gcloud iam service-accounts describe "${name}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    gcloud iam service-accounts create "${name}" --display-name="${display}"
  fi
}

create_service_account searcharis-ingress "Searcharis ingress"
create_service_account searcharis-worker "Searcharis worker"
create_service_account searcharis-pubsub-invoker "Searcharis Pub/Sub invoker"
create_service_account searcharis-tasks-invoker "Searcharis Tasks invoker"

INGRESS_SA="searcharis-ingress@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_SA="searcharis-worker@${PROJECT_ID}.iam.gserviceaccount.com"
TASKS_INVOKER_SA="searcharis-tasks-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

for role in roles/pubsub.publisher roles/datastore.user; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${INGRESS_SA}" \
    --role="${role}" \
    --quiet >/dev/null
done

for role in roles/datastore.user roles/cloudtasks.enqueuer roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${WORKER_SA}" \
    --role="${role}" \
    --quiet >/dev/null
done

gcloud iam service-accounts add-iam-policy-binding "${TASKS_INVOKER_SA}" \
  --member="serviceAccount:${WORKER_SA}" \
  --role="roles/iam.serviceAccountUser" \
  --quiet >/dev/null

if ! gcloud pubsub topics describe "${TOPIC}" >/dev/null 2>&1; then
  gcloud pubsub topics create "${TOPIC}"
fi

if ! gcloud tasks queues describe "${QUEUE}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud tasks queues create "${QUEUE}" --location="${REGION}" --log-sampling-ratio=1.0
fi

if ! gcloud firestore databases describe --database="${DATABASE_ID}" >/dev/null 2>&1; then
  gcloud firestore databases create \
    --database="${DATABASE_ID}" \
    --location="${REGION}" \
    --edition=standard \
    --type=firestore-native
fi

# Operational collections use native expires_at timestamps. TTL provisioning is
# intentionally human-admin owned instead of granting schema-admin IAM to the
# GitHub deployer. Updates are asynchronous because Firestore TTL policy changes
# can take several minutes to finish provisioning.
for collection_group in events runs evidence actions; do
  gcloud firestore fields ttls update expires_at \
    --database="${DATABASE_ID}" \
    --collection-group="${collection_group}" \
    --enable-ttl \
    --async \
    --quiet >/dev/null
done

# The active-incident query filters repository + affected_url and orders by
# updated_at. Provision exactly that composite index, and avoid duplicate create
# operations on repeated bootstrap runs.
if ! gcloud firestore indexes composite list \
  --database="${DATABASE_ID}" \
  --filter='collectionGroup:incidents' \
  --format=json \
  | python3 -c '
import json, sys
indexes = json.load(sys.stdin)
for index in indexes:
    fields = {item.get("fieldPath"): item.get("order") for item in index.get("fields", [])}
    if (
        fields.get("repository") == "ASCENDING"
        and fields.get("affected_url") == "ASCENDING"
        and fields.get("updated_at") == "DESCENDING"
    ):
        raise SystemExit(0)
raise SystemExit(1)
'; then
  gcloud firestore indexes composite create \
    --database="${DATABASE_ID}" \
    --collection-group=incidents \
    --query-scope=collection \
    --field-config=field-path=repository,order=ascending \
    --field-config=field-path=affected_url,order=ascending \
    --field-config=field-path=updated_at,order=descending \
    --async \
    --quiet >/dev/null
fi

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
PUBSUB_SERVICE_AGENT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${COMPUTE_SA}" \
  --role="roles/run.builder" \
  --quiet >/dev/null

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${PUBSUB_SERVICE_AGENT}" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --quiet >/dev/null

cat <<OUT
Bootstrap complete.
Project: ${PROJECT_ID}
Region: ${REGION}
Topic: ${TOPIC}
Queue: ${QUEUE}
Firestore TTL: events/runs/evidence/actions on expires_at
Firestore incident index: repository + affected_url + updated_at desc

Before deployment, create these Secret Manager secrets with at least one enabled version:
  searcharis-github-token
  searcharis-webhook-secret
  searcharis-demo-token
OUT
