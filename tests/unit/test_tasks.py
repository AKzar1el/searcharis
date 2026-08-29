import json
from datetime import UTC, datetime

import pytest

from searcharis.integrations.tasks import VerificationScheduler, verification_task_id


class FakeTasksClient:
    def __init__(self):
        self.requests = []

    def queue_path(self, project, location, queue):
        return f"projects/{project}/locations/{location}/queues/{queue}"

    def create_task(self, request):
        self.requests.append(request)
        return request["task"]


def test_verification_task_id_is_deterministic_and_bounded():
    first = verification_task_id("event-1", "incident-1")
    second = verification_task_id("event-1", "incident-1")
    assert first == second
    assert first.startswith("verify-")
    assert len(first) == len("verify-") + 32


@pytest.mark.asyncio
async def test_scheduler_builds_minimal_oidc_task_and_clamps_delay():
    client = FakeTasksClient()
    now = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)
    scheduler = VerificationScheduler(
        project_id="project-1",
        region="us-central1",
        queue="searcharis-verification",
        worker_base_url="https://worker.example",
        service_account_email="tasks@example.iam.gserviceaccount.com",
        client=client,
        clock=lambda: now,
    )

    task_name = await scheduler.schedule("event-1", "incident-1", 5)

    request = client.requests[0]
    task = request["task"]
    assert task_name.endswith(verification_task_id("event-1", "incident-1"))
    assert request["parent"] == "projects/project-1/locations/us-central1/queues/searcharis-verification"
    assert task["http_request"]["url"] == "https://worker.example/internal/verify"
    assert task["http_request"]["headers"] == {"Content-Type": "application/json"}
    assert json.loads(task["http_request"]["body"]) == {
        "event_id": "event-1",
        "incident_id": "incident-1",
    }
    assert task["http_request"]["oidc_token"] == {
        "service_account_email": "tasks@example.iam.gserviceaccount.com",
        "audience": "https://worker.example",
    }
    scheduled = task["schedule_time"].ToDatetime(tzinfo=UTC)
    assert (scheduled - now).total_seconds() == 15
