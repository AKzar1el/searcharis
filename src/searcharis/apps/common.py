from __future__ import annotations

import asyncio
from typing import Any

from searcharis.agent.diagnostician import Diagnostician
from searcharis.config import Settings
from searcharis.integrations.github import GitHubIssueBroker
from searcharis.integrations.tasks import VerificationScheduler
from searcharis.integrations.validator import ValidatorClient
from searcharis.services.orchestrator import Orchestrator
from searcharis.storage.firestore import FirestoreStateStore


class GooglePubSubPublisher:
    def __init__(self, project_id: str, topic: str, client: Any | None = None) -> None:
        if client is None:
            try:
                from google.cloud import pubsub_v1  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - deployed dependency
                raise RuntimeError("google-cloud-pubsub is required for ingress publishing") from exc
            client = pubsub_v1.PublisherClient()
        self._client = client
        self._topic_path = client.topic_path(project_id, topic)

    async def publish(self, event: Any) -> str:
        payload = event.model_dump_json().encode("utf-8")
        future = self._client.publish(self._topic_path, payload)
        return str(await asyncio.to_thread(future.result))


def build_store(settings: Settings):
    if not settings.project_id:
        raise RuntimeError("SEARCHARIS_PROJECT_ID is required in cloud mode")
    return FirestoreStateStore(settings.project_id)


def build_ingress_dependencies(settings: Settings):
    if not settings.project_id:
        raise RuntimeError("SEARCHARIS_PROJECT_ID is required in cloud mode")
    return build_store(settings), GooglePubSubPublisher(settings.project_id, settings.pubsub_topic)


def build_worker_orchestrator(settings: Settings) -> Orchestrator:
    required = {
        "project_id": settings.project_id,
        "worker_url": settings.worker_url,
        "tasks_invoker_service_account": settings.tasks_invoker_service_account,
        "github_token": settings.github_token,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing worker configuration: {', '.join(missing)}")
    store = build_store(settings)
    return Orchestrator(
        store=store,
        validator=ValidatorClient(str(settings.validator_mcp_url)),
        diagnostician=Diagnostician(),
        github=GitHubIssueBroker(settings.github_token),
        scheduler=VerificationScheduler(
            project_id=settings.project_id,
            region=settings.region,
            queue=settings.tasks_queue,
            worker_base_url=str(settings.worker_url),
            service_account_email=settings.tasks_invoker_service_account,
        ),
    )


def health_payload(service: str) -> dict[str, str]:
    return {"status": "ok", "service": service, "product": "Searcharis"}
