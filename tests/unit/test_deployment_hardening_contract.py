from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_pubsub_push_subscription_has_backoff_and_dead_letter_policy():
    deploy = _read("deployment/deploy-services.sh")

    assert "searcharis-deployments-dead-letter" in deploy
    assert "searcharis-deployments-dead-letter-retention" in deploy
    assert "--min-retry-delay=10s" in deploy
    assert "--max-retry-delay=60s" in deploy
    assert "--max-delivery-attempts=8" in deploy
    assert "--dead-letter-topic=" in deploy
    assert "roles/pubsub.publisher" in deploy
    assert "roles/pubsub.subscriber" in deploy


def test_firestore_admin_bootstrap_declares_ttl_and_incident_index():
    bootstrap = _read("deployment/bootstrap.sh")

    for collection_group in ("events", "runs", "evidence", "actions"):
        assert collection_group in bootstrap
    assert "firestore fields ttls update expires_at" in bootstrap
    assert "--enable-ttl" in bootstrap
    assert "firestore indexes composite create" in bootstrap
    assert "field-path=repository,order=ascending" in bootstrap
    assert "field-path=affected_url,order=ascending" in bootstrap
    assert "field-path=updated_at,order=descending" in bootstrap


def test_cloud_run_limits_are_explicit_and_backpressure_worker():
    deploy = _read("deployment/deploy-services.sh")

    assert 'WORKER_MAX_INSTANCES="${SEARCHARIS_WORKER_MAX_INSTANCES:-2}"' in deploy
    assert 'WORKER_CONCURRENCY="${SEARCHARIS_WORKER_CONCURRENCY:-4}"' in deploy
    assert 'INGRESS_MAX_INSTANCES="${SEARCHARIS_INGRESS_MAX_INSTANCES:-3}"' in deploy
    assert 'INGRESS_CONCURRENCY="${SEARCHARIS_INGRESS_CONCURRENCY:-20}"' in deploy
    assert 'WORKER_CPU="${SEARCHARIS_WORKER_CPU:-1}"' in deploy
    assert 'WORKER_MEMORY="${SEARCHARIS_WORKER_MEMORY:-1Gi}"' in deploy
    assert 'WORKER_TIMEOUT="${SEARCHARIS_WORKER_TIMEOUT:-600s}"' in deploy
    assert 'INGRESS_CPU="${SEARCHARIS_INGRESS_CPU:-1}"' in deploy
    assert 'INGRESS_MEMORY="${SEARCHARIS_INGRESS_MEMORY:-512Mi}"' in deploy
    assert 'INGRESS_TIMEOUT="${SEARCHARIS_INGRESS_TIMEOUT:-60s}"' in deploy
    assert deploy.count("--cpu-boost") >= 2


def test_smoke_verifies_delivery_and_cloud_run_configuration():
    smoke = _read("deployment/smoke.sh")

    assert "deadLetterPolicy" in smoke
    assert "retryPolicy" in smoke
    assert "maxDeliveryAttempts" in smoke
    assert "minimumBackoff" in smoke
    assert "maximumBackoff" in smoke
    assert "containerConcurrency" in smoke
    assert "timeoutSeconds" in smoke
    assert "startup-cpu-boost" in smoke
