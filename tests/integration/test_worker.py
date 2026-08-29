import base64
import json

from fastapi.testclient import TestClient

from searcharis.apps.worker import create_worker_app
from searcharis.models import RunRecord, WorkflowState


class FakeOrchestrator:
    def __init__(self, state=WorkflowState.VERIFYING):
        self.state = state
        self.processed = []
        self.verified = []

    async def process_deployment(self, event):
        self.processed.append(event)
        return RunRecord(run_id="run-1", event_id=event.event_id, state=self.state)

    async def verify(self, event_id, incident_id):
        self.verified.append((event_id, incident_id))
        return RunRecord(run_id="run-2", event_id=event_id, state=self.state)


def pubsub_payload():
    event = {
        "event_id": "evt-1",
        "repository": "AKzar1el/searcharis-demo",
        "target_url": "https://demo.example/",
        "commit_sha": "1234567",
        "source": "demo",
    }
    encoded = base64.b64encode(json.dumps(event).encode()).decode()
    return {"message": {"data": encoded}}


def test_ready_endpoint_uses_cloud_run_safe_path():
    client = TestClient(create_worker_app(FakeOrchestrator()))
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "worker",
        "product": "Searcharis",
    }


def test_pubsub_envelope_invokes_orchestrator_and_returns_204():
    orchestrator = FakeOrchestrator()
    client = TestClient(create_worker_app(orchestrator))
    response = client.post("/internal/pubsub", json=pubsub_payload())
    assert response.status_code == 204
    assert len(orchestrator.processed) == 1


def test_retryable_run_returns_500_for_pubsub_redelivery():
    orchestrator = FakeOrchestrator(WorkflowState.FAILED_RETRYABLE)
    client = TestClient(create_worker_app(orchestrator))
    response = client.post("/internal/pubsub", json=pubsub_payload())
    assert response.status_code == 500


def test_verification_endpoint_invokes_exact_incident():
    orchestrator = FakeOrchestrator(WorkflowState.RESOLVED)
    client = TestClient(create_worker_app(orchestrator))
    response = client.post(
        "/internal/verify", json={"event_id": "evt-1", "incident_id": "inc-1"}
    )
    assert response.status_code == 204
    assert orchestrator.verified == [("evt-1", "inc-1")]
