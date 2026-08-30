#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
INGRESS_SERVICE="${SEARCHARIS_INGRESS_SERVICE:-searcharis-ingress}"
WORKER_SERVICE="${SEARCHARIS_WORKER_SERVICE:-searcharis-worker}"
DEMO_SERVICE="${SEARCHARIS_DEMO_SERVICE:-searcharis-demo-target}"
TOPIC="${SEARCHARIS_PUBSUB_TOPIC:-searcharis-deployments}"
SUBSCRIPTION="${SEARCHARIS_PUBSUB_SUBSCRIPTION:-searcharis-worker-push}"
DLQ_TOPIC="${SEARCHARIS_PUBSUB_DLQ_TOPIC:-searcharis-deployments-dead-letter}"
DLQ_SUBSCRIPTION="${SEARCHARIS_PUBSUB_DLQ_SUBSCRIPTION:-searcharis-deployments-dead-letter-retention}"
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

echo "Smoke Pub/Sub retryPolicy and deadLetterPolicy"
DEAD_LETTER_TOPIC="$(gcloud pubsub subscriptions describe "${SUBSCRIPTION}" --format='value(deadLetterPolicy.deadLetterTopic)')"
MAX_DELIVERY_ATTEMPTS="$(gcloud pubsub subscriptions describe "${SUBSCRIPTION}" --format='value(deadLetterPolicy.maxDeliveryAttempts)')"
MINIMUM_BACKOFF="$(gcloud pubsub subscriptions describe "${SUBSCRIPTION}" --format='value(retryPolicy.minimumBackoff)')"
MAXIMUM_BACKOFF="$(gcloud pubsub subscriptions describe "${SUBSCRIPTION}" --format='value(retryPolicy.maximumBackoff)')"

if [[ "${DEAD_LETTER_TOPIC##*/}" != "${DLQ_TOPIC}" ]]; then
  echo "Pub/Sub dead-letter topic mismatch: ${DEAD_LETTER_TOPIC}" >&2
  exit 1
fi
if [[ "${MAX_DELIVERY_ATTEMPTS}" != "8" ]]; then
  echo "Pub/Sub maxDeliveryAttempts mismatch: ${MAX_DELIVERY_ATTEMPTS}" >&2
  exit 1
fi
if [[ "${MINIMUM_BACKOFF}" != "10s" ]]; then
  echo "Pub/Sub minimumBackoff mismatch: ${MINIMUM_BACKOFF}" >&2
  exit 1
fi
if [[ "${MAXIMUM_BACKOFF}" != "60s" ]]; then
  echo "Pub/Sub maximumBackoff mismatch: ${MAXIMUM_BACKOFF}" >&2
  exit 1
fi
gcloud pubsub subscriptions describe "${DLQ_SUBSCRIPTION}" >/dev/null
echo "Smoke Pub/Sub delivery policy: ok"

verify_cloud_run_service() {
  local service="$1"
  local expected_concurrency="$2"
  local expected_timeout="$3"
  local expected_cpu="$4"
  local expected_memory="$5"
  local expected_max="$6"

  SERVICE_CONFIG_JSON="$(gcloud run services describe "${service}" --region="${REGION}" --format=json)" \
  EXPECTED_CONCURRENCY="${expected_concurrency}" \
  EXPECTED_TIMEOUT="${expected_timeout}" \
  EXPECTED_CPU="${expected_cpu}" \
  EXPECTED_MEMORY="${expected_memory}" \
  EXPECTED_MAX="${expected_max}" \
  python3 - <<'PY'
import json
import os

config = json.loads(os.environ["SERVICE_CONFIG_JSON"])
template = config.get("spec", {}).get("template", {})
spec = template.get("spec", {})
template_annotations = template.get("metadata", {}).get("annotations", {})
service_annotations = config.get("metadata", {}).get("annotations", {})
service_scaling = config.get("scaling", {})
containers = spec.get("containers", [])
if not containers:
    raise SystemExit("Cloud Run service has no container configuration")
limits = containers[0].get("resources", {}).get("limits", {})

containerConcurrency = spec.get("containerConcurrency")
timeoutSeconds = spec.get("timeoutSeconds")
startup_cpu_boost = template_annotations.get("run.googleapis.com/startup-cpu-boost")
max_scale = service_scaling.get("maxInstanceCount")
if max_scale is None:
    max_scale = service_annotations.get("run.googleapis.com/maxScale")

expected_concurrency = int(os.environ["EXPECTED_CONCURRENCY"])
expected_timeout = int(os.environ["EXPECTED_TIMEOUT"])
expected_cpu = os.environ["EXPECTED_CPU"]
expected_memory = os.environ["EXPECTED_MEMORY"]
expected_max = os.environ["EXPECTED_MAX"]

cpu = str(limits.get("cpu", ""))
normalized_cpu = "1" if cpu in {"1", "1000m"} else cpu

checks = {
    "containerConcurrency": (containerConcurrency, expected_concurrency),
    "timeoutSeconds": (timeoutSeconds, expected_timeout),
    "cpu": (normalized_cpu, expected_cpu),
    "memory": (str(limits.get("memory", "")), expected_memory),
    "serviceMaxScale": (str(max_scale), expected_max),
    "startup-cpu-boost": (str(startup_cpu_boost).lower(), "true"),
}
failures = [f"{name}={actual!r} expected {expected!r}" for name, (actual, expected) in checks.items() if actual != expected]
if failures:
    raise SystemExit("Cloud Run configuration mismatch: " + "; ".join(failures))
PY
}

echo "Smoke Cloud Run resource/backpressure settings"
verify_cloud_run_service "${WORKER_SERVICE}" 4 600 1 1Gi 2
verify_cloud_run_service "${INGRESS_SERVICE}" 20 60 1 512Mi 3
echo "Smoke Cloud Run settings: ok"

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
