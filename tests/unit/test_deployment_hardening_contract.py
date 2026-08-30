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


def test_pubsub_dead_letter_iam_is_human_admin_owned():
    bootstrap = _read("deployment/bootstrap.sh")
    deploy = _read("deployment/deploy-services.sh")

    assert "searcharis-deployments-dead-letter" in bootstrap
    assert "roles/pubsub.publisher" in bootstrap
    assert "roles/pubsub.subscriber" in bootstrap
    assert "add-iam-policy-binding" not in "\n".join(
        line
        for line in deploy.splitlines()
        if "pubsub topics" in line or "pubsub subscriptions" in line or "roles/pubsub" in line
    )


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


def test_readme_and_operations_doc_make_judging_deadline_explicit():
    readme = _read("README.md")
    operations = _read("docs/JUDGING-OPERATIONS.md")

    for content in (readme, operations):
        assert "October 1, 2026" in content
        assert "11:45 PM PT" in content
        assert "searcharis-ingress-2wzjcu6mqa-uc.a.run.app" in content
    assert "docs/JUDGING-OPERATIONS.md" in readme
    assert "Do not" in operations or "DO NOT" in operations


def test_deployment_warns_when_multiple_github_token_versions_are_enabled():
    deploy = _read("deployment/deploy-services.sh")

    assert "searcharis-github-token" in deploy
    assert "multiple enabled" in deploy.lower()
    assert "secret payload" in deploy.lower()


def test_successful_deploy_summary_repeats_no_teardown_deadline():
    workflow = _read(".github/workflows/deploy-gcp.yml")

    assert "KEEP LIVE THROUGH 2026-10-01 23:45 PT" in workflow
    assert "searcharis-ingress" in workflow
