import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from searcharis.apps.ingress import IngressRuntime, create_ingress_app
from searcharis.storage.memory import InMemoryStateStore


class FakePublisher:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)
        return "message-1"


def runtime():
    return IngressRuntime(
        store=InMemoryStateStore(),
        publisher=FakePublisher(),
        webhook_secret="secret",
        demo_token="demo-secret",
        demo_repository="AKzar1el/searcharis-demo",
        demo_target_url="https://demo.example/",
    )


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()


def test_ready_endpoint_uses_cloud_run_safe_path():
    client = TestClient(create_ingress_app(runtime()))
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["mode"] == "ingress"


def test_github_webhook_rejects_bad_signature_before_json_parsing():
    rt = runtime()
    client = TestClient(create_ingress_app(rt))
    response = client.post(
        "/webhooks/github",
        content=b"not-json",
        headers={"X-Hub-Signature-256": "sha256=bad"},
    )
    assert response.status_code == 401
    assert rt.publisher.events == []


def test_successful_deployment_status_is_normalized_and_published():
    rt = runtime()
    client = TestClient(create_ingress_app(rt))
    payload = {
        "deployment_status": {
            "state": "success",
            "target_url": "https://ci.example/deployments/42/logs",
            "environment_url": "https://demo.example/",
        },
        "deployment": {"sha": "1234567890abcdef"},
        "repository": {"full_name": "AKzar1el/searcharis-demo"},
    }
    raw = json.dumps(payload).encode()
    response = client.post(
        "/webhooks/github",
        content=raw,
        headers={
            "X-Hub-Signature-256": sign(raw),
            "X-GitHub-Event": "deployment_status",
            "X-GitHub-Delivery": "delivery-1",
        },
    )
    assert response.status_code == 202
    assert len(rt.publisher.events) == 1
    event = rt.publisher.events[0]
    assert event.event_id == "delivery-1"
    assert event.repository == "AKzar1el/searcharis-demo"
    assert str(event.target_url) == "https://demo.example/"


def test_demo_endpoint_rejects_arbitrary_target_and_accepts_configured_target():
    rt = runtime()
    client = TestClient(create_ingress_app(rt))

    rejected = client.post(
        "/demo/events",
        json={"repository": "AKzar1el/searcharis-demo", "target_url": "https://evil.example/"},
        headers={"X-Searcharis-Demo-Token": "demo-secret"},
    )
    accepted = client.post(
        "/demo/events",
        json={
            "repository": "AKzar1el/searcharis-demo",
            "target_url": "https://demo.example/",
            "commit_sha": "abcdef1234567",
        },
        headers={"X-Searcharis-Demo-Token": "demo-secret"},
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 202
    assert len(rt.publisher.events) == 1
