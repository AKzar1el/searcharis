#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
POOL_ID="${SEARCHARIS_WIF_POOL_ID:-github-actions}"
PROVIDER_ID="${SEARCHARIS_WIF_PROVIDER_ID:-searcharis}"
DEPLOYER_NAME="${SEARCHARIS_DEPLOYER_SERVICE_ACCOUNT:-searcharis-deployer}"
DEPLOY_BRANCH="${SEARCHARIS_DEPLOY_BRANCH:-main}"
GITHUB_REPOSITORY="${SEARCHARIS_GITHUB_REPOSITORY:-AKzar1el/searcharis}"
GITHUB_REPOSITORY_ID="${SEARCHARIS_GITHUB_REPOSITORY_ID:-1350136483}"
GITHUB_OWNER_ID="${SEARCHARIS_GITHUB_OWNER_ID:-104433268}"
WORKFLOW_PATH="${SEARCHARIS_DEPLOY_WORKFLOW_PATH:-.github/workflows/deploy-gcp.yml}"

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "GOOGLE_CLOUD_PROJECT is required" >&2
  exit 2
fi

gcloud config set project "${PROJECT_ID}" >/dev/null
export GOOGLE_CLOUD_PROJECT="${PROJECT_ID}"
export GOOGLE_CLOUD_LOCATION="${REGION}"

# One-time Google Cloud foundation owned by the human project administrator.
./deployment/bootstrap.sh

gcloud services enable \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com >/dev/null

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
DEPLOYER_SA="${DEPLOYER_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
INGRESS_SA="searcharis-ingress@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_SA="searcharis-worker@${PROJECT_ID}.iam.gserviceaccount.com"
PUBSUB_INVOKER_SA="searcharis-pubsub-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
TASKS_INVOKER_SA="searcharis-tasks-invoker@${PROJECT_ID}.iam.gserviceaccount.com"

if ! gcloud iam service-accounts describe "${DEPLOYER_SA}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${DEPLOYER_NAME}" \
    --display-name="Searcharis GitHub deployer"
fi

# The deployer can deploy/update the existing Searcharis Cloud Run topology,
# configure its Pub/Sub push subscription, and inspect secret metadata. It
# never receives Secret Manager payload access.
for role in \
  roles/run.admin \
  roles/run.sourceDeveloper \
  roles/serviceusage.serviceUsageConsumer \
  roles/pubsub.editor \
  roles/secretmanager.viewer; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${DEPLOYER_SA}" \
    --role="${role}" \
    --quiet >/dev/null
done

# Source deployment and authenticated push configuration require actAs on the
# service identities they explicitly reference. Grant this only on those SAs.
for service_account in \
  "${COMPUTE_SA}" \
  "${INGRESS_SA}" \
  "${WORKER_SA}" \
  "${PUBSUB_INVOKER_SA}" \
  "${TASKS_INVOKER_SA}"; do
  gcloud iam service-accounts add-iam-policy-binding "${service_account}" \
    --member="serviceAccount:${DEPLOYER_SA}" \
    --role="roles/iam.serviceAccountUser" \
    --quiet >/dev/null
done

if ! gcloud iam workload-identity-pools describe "${POOL_ID}" \
  --location=global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${POOL_ID}" \
    --location=global \
    --display-name="GitHub Actions"
fi

WORKFLOW_REF="${GITHUB_REPOSITORY}/${WORKFLOW_PATH}@refs/heads/${DEPLOY_BRANCH}"
ATTRIBUTE_MAPPING="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.repository_owner_id=assertion.repository_owner_id,attribute.ref=assertion.ref,attribute.workflow_ref=assertion.workflow_ref"
ATTRIBUTE_CONDITION="assertion.repository_id=='${GITHUB_REPOSITORY_ID}' && assertion.repository_owner_id=='${GITHUB_OWNER_ID}' && assertion.ref=='refs/heads/${DEPLOY_BRANCH}' && assertion.workflow_ref=='${WORKFLOW_REF}'"

if ! gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
  --location=global \
  --workload-identity-pool="${POOL_ID}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
    --location=global \
    --workload-identity-pool="${POOL_ID}" \
    --issuer-uri="https://token.actions.githubusercontent.com/" \
    --attribute-mapping="${ATTRIBUTE_MAPPING}" \
    --attribute-condition="${ATTRIBUTE_CONDITION}"
else
  gcloud iam workload-identity-pools providers update-oidc "${PROVIDER_ID}" \
    --location=global \
    --workload-identity-pool="${POOL_ID}" \
    --attribute-mapping="${ATTRIBUTE_MAPPING}" \
    --attribute-condition="${ATTRIBUTE_CONDITION}" \
    --quiet >/dev/null
fi

POOL_RESOURCE="$(gcloud iam workload-identity-pools describe "${POOL_ID}" \
  --location=global \
  --format='value(name)')"
PROVIDER_RESOURCE="$(gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
  --location=global \
  --workload-identity-pool="${POOL_ID}" \
  --format='value(name)')"

WIF_MEMBER="principalSet://iam.googleapis.com/${POOL_RESOURCE}/attribute.repository_id/${GITHUB_REPOSITORY_ID}"
gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="${WIF_MEMBER}" \
  --quiet >/dev/null

secret_has_enabled_version() {
  local secret="$1"
  [[ -n "$(gcloud secrets versions list "${secret}" \
    --filter='state=ENABLED' \
    --limit=1 \
    --format='value(name)' 2>/dev/null || true)" ]]
}

ensure_secret_value() {
  local secret="$1"
  local value="$2"
  if ! gcloud secrets describe "${secret}" >/dev/null 2>&1; then
    printf '%s' "${value}" | gcloud secrets create "${secret}" \
      --replication-policy=automatic \
      --data-file=- >/dev/null
  elif ! secret_has_enabled_version "${secret}"; then
    printf '%s' "${value}" | gcloud secrets versions add "${secret}" \
      --data-file=- >/dev/null
  fi
}

if ! secret_has_enabled_version searcharis-github-token; then
  printf 'Create a fine-grained GitHub token restricted to %s with Issues: read/write.\n' "${GITHUB_REPOSITORY}"
  read -rsp 'Paste that token here (input hidden): ' GITHUB_TOKEN
  echo
  if [[ -z "${GITHUB_TOKEN}" ]]; then
    echo "GitHub token cannot be empty" >&2
    exit 2
  fi
  ensure_secret_value searcharis-github-token "${GITHUB_TOKEN}"
  unset GITHUB_TOKEN
fi

if ! secret_has_enabled_version searcharis-webhook-secret; then
  ensure_secret_value searcharis-webhook-secret "$(openssl rand -hex 32)"
fi

if ! secret_has_enabled_version searcharis-demo-token; then
  ensure_secret_value searcharis-demo-token "$(openssl rand -hex 32)"
fi

# Runtime services, not the GitHub deployer, receive payload access.
gcloud secrets add-iam-policy-binding searcharis-github-token \
  --member="serviceAccount:${WORKER_SA}" \
  --role="roles/secretmanager.secretAccessor" \
  --quiet >/dev/null

gcloud secrets add-iam-policy-binding searcharis-webhook-secret \
  --member="serviceAccount:${INGRESS_SA}" \
  --role="roles/secretmanager.secretAccessor" \
  --quiet >/dev/null

gcloud secrets add-iam-policy-binding searcharis-demo-token \
  --member="serviceAccount:${INGRESS_SA}" \
  --role="roles/secretmanager.secretAccessor" \
  --quiet >/dev/null

cat <<OUT

GitHub -> Google Cloud WIF bootstrap complete.

Project: ${PROJECT_ID} (${PROJECT_NUMBER})
Cloud Run region: ${REGION}
Vertex location: global
Deploy branch: ${DEPLOY_BRANCH}
Deployer service account: ${DEPLOYER_SA}
Workload Identity Provider:
  ${PROVIDER_RESOURCE}

Trust is restricted to:
  repository_id=${GITHUB_REPOSITORY_ID}
  repository_owner_id=${GITHUB_OWNER_ID}
  ref=refs/heads/${DEPLOY_BRANCH}
  workflow_ref=${WORKFLOW_REF}

No Google service-account key was created.
OUT
