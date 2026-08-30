#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
VERTEX_LOCATION="${SEARCHARIS_VERTEX_LOCATION:-global}"
TOPIC="${SEARCHARIS_PUBSUB_TOPIC:-searcharis-deployments}"
SUBSCRIPTION="${SEARCHARIS_PUBSUB_SUBSCRIPTION:-searcharis-worker-push}"
QUEUE="${SEARCHARIS_TASKS_QUEUE:-searcharis-verification}"
DEMO_REPOSITORY="${SEARCHARIS_DEMO_REPOSITORY:?SEARCHARIS_DEMO_REPOSITORY is required}"
DEMO_TARGET_URL="${SEARCHARIS_DEMO_TARGET_URL:?SEARCHARIS_DEMO_TARGET_URL is required}"
VALIDATOR_URL="${SEARCHARIS_VALIDATOR_MCP_URL:-https://web-validator-mcp.digestseo.com/mcp}"
SKIP_SECRET_IAM="${SEARCHARIS_SKIP_SECRET_IAM:-false}"

INGRESS_SERVICE="${SEARCHARIS_INGRESS_SERVICE:-searcharis-ingress}"
WORKER_SERVICE="${SEARCHARIS_WORKER_SERVICE:-searcharis-worker}"
INGRESS_SA="searcharis-ingress@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_SA="searcharis-worker@${PROJECT_ID}.iam.gserviceaccount.com"
PUBSUB_INVOKER_SA="searcharis-pubsub-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
TASKS_INVOKER_SA="searcharis-tasks-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
INGRESS_MAX_INSTANCES="${SEARCHARIS_INGRESS_MAX_INSTANCES:-3}"
WORKER_MAX_INSTANCES="${SEARCHARIS_WORKER_MAX_INSTANCES:-3}"
INGRESS_CONCURRENCY="${SEARCHARIS_INGRESS_CONCURRENCY:-20}"
WORKER_CONCURRENCY="${SEARCHARIS_WORKER_CONCURRENCY:-8}"

for secret in searcharis-github-token searcharis-webhook-secret searcharis-demo-token; do
  enabled_version="$(gcloud secrets versions list "${secret}" \
    --filter='state=ENABLED' \
    --limit=1 \
    --format='value(name)' 2>/dev/null || true)"
  if [[ -z "${enabled_version}" ]]; then
    echo "Secret ${secret} must exist with an enabled version before deployment." >&2
    exit 2
  fi
done

if [[ "${SKIP_SECRET_IAM}" != "true" ]]; then
  for pair in \
    "${WORKER_SA}:searcharis-github-token" \
    "${INGRESS_SA}:searcharis-webhook-secret" \
    "${INGRESS_SA}:searcharis-demo-token"; do
    sa="${pair%%:*}"
    secret="${pair#*:}"
    gcloud secrets add-iam-policy-binding "${secret}" \
      --member="serviceAccount:${sa}" \
      --role="roles/secretmanager.secretAccessor" \
      --quiet >/dev/null
  done
fi

COMMON_WORKER_ENV="SERVICE_MODE=worker,SEARCHARIS_PROJECT_ID=${PROJECT_ID},SEARCHARIS_REGION=${REGION},SEARCHARIS_TASKS_QUEUE=${QUEUE},SEARCHARIS_VALIDATOR_MCP_URL=${VALIDATOR_URL},SEARCHARIS_TASKS_INVOKER_SERVICE_ACCOUNT=${TASKS_INVOKER_SA},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${VERTEX_LOCATION},GOOGLE_GENAI_USE_VERTEXAI=TRUE"

gcloud run deploy "${WORKER_SERVICE}" \
  --source . \
  --region="${REGION}" \
  --service-account="${WORKER_SA}" \
  --no-allow-unauthenticated \
  --min=0 \
  --max="${WORKER_MAX_INSTANCES}" \
  --concurrency="${WORKER_CONCURRENCY}" \
  --set-env-vars="${COMMON_WORKER_ENV},SEARCHARIS_WORKER_URL=https://bootstrap.invalid" \
  --set-secrets="SEARCHARIS_GITHUB_TOKEN=searcharis-github-token:latest" \
  --quiet

WORKER_URL="$(gcloud run services describe "${WORKER_SERVICE}" --region="${REGION}" --format='value(status.url)')"

gcloud run services update "${WORKER_SERVICE}" \
  --region="${REGION}" \
  --update-env-vars="SEARCHARIS_WORKER_URL=${WORKER_URL}" \
  --quiet

gcloud run services add-iam-policy-binding "${WORKER_SERVICE}" \
  --region="${REGION}" \
  --member="serviceAccount:${PUBSUB_INVOKER_SA}" \
  --role="roles/run.invoker" \
  --quiet >/dev/null

gcloud run services add-iam-policy-binding "${WORKER_SERVICE}" \
  --region="${REGION}" \
  --member="serviceAccount:${TASKS_INVOKER_SA}" \
  --role="roles/run.invoker" \
  --quiet >/dev/null

if gcloud pubsub subscriptions describe "${SUBSCRIPTION}" >/dev/null 2>&1; then
  gcloud pubsub subscriptions modify-push-config "${SUBSCRIPTION}" \
    --push-endpoint="${WORKER_URL}/internal/pubsub" \
    --push-auth-service-account="${PUBSUB_INVOKER_SA}" \
    --push-auth-token-audience="${WORKER_URL}"
else
  gcloud pubsub subscriptions create "${SUBSCRIPTION}" \
    --topic="${TOPIC}" \
    --ack-deadline=600 \
    --push-endpoint="${WORKER_URL}/internal/pubsub" \
    --push-auth-service-account="${PUBSUB_INVOKER_SA}" \
    --push-auth-token-audience="${WORKER_URL}"
fi

INGRESS_ENV="SERVICE_MODE=ingress,SEARCHARIS_PROJECT_ID=${PROJECT_ID},SEARCHARIS_REGION=${REGION},SEARCHARIS_PUBSUB_TOPIC=${TOPIC},SEARCHARIS_DEMO_REPOSITORY=${DEMO_REPOSITORY},SEARCHARIS_DEMO_TARGET_URL=${DEMO_TARGET_URL}"

gcloud run deploy "${INGRESS_SERVICE}" \
  --source . \
  --region="${REGION}" \
  --service-account="${INGRESS_SA}" \
  --allow-unauthenticated \
  --min=0 \
  --max="${INGRESS_MAX_INSTANCES}" \
  --concurrency="${INGRESS_CONCURRENCY}" \
  --set-env-vars="${INGRESS_ENV}" \
  --set-secrets="SEARCHARIS_WEBHOOK_SECRET=searcharis-webhook-secret:latest,SEARCHARIS_DEMO_TOKEN=searcharis-demo-token:latest" \
  --quiet

INGRESS_URL="$(gcloud run services describe "${INGRESS_SERVICE}" --region="${REGION}" --format='value(status.url)')"
printf 'Ingress: %s\nWorker: %s\n' "${INGRESS_URL}" "${WORKER_URL}"
