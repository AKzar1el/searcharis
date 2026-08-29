from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from google.protobuf.timestamp_pb2 import Timestamp


def verification_task_id(event_id: str, incident_id: str) -> str:
    digest = hashlib.sha256(f"{incident_id}\0{event_id}".encode()).hexdigest()[:32]
    return f"verify-{digest}"


class VerificationScheduler:
    def __init__(
        self,
        *,
        project_id: str,
        region: str,
        queue: str,
        worker_base_url: str,
        service_account_email: str,
        client: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if client is None:
            try:
                from google.cloud import tasks_v2  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - deployed dependency
                raise RuntimeError("google-cloud-tasks is required for VerificationScheduler") from exc
            client = tasks_v2.CloudTasksClient()
        self._client = client
        self._project_id = project_id
        self._region = region
        self._queue = queue
        self._worker_base_url = worker_base_url.rstrip("/")
        self._service_account_email = service_account_email
        self._clock = clock or (lambda: datetime.now(UTC))

    async def schedule(self, event_id: str, incident_id: str, delay_seconds: int) -> str:
        bounded_delay = max(15, min(int(delay_seconds), 900))
        parent = self._client.queue_path(self._project_id, self._region, self._queue)
        task_id = verification_task_id(event_id, incident_id)
        task_name = f"{parent}/tasks/{task_id}"

        schedule_time = Timestamp()
        schedule_time.FromDatetime(self._clock() + timedelta(seconds=bounded_delay))
        body = json.dumps(
            {"event_id": event_id, "incident_id": incident_id},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        task = {
            "name": task_name,
            "schedule_time": schedule_time,
            "http_request": {
                "http_method": "POST",
                "url": f"{self._worker_base_url}/internal/verify",
                "headers": {"Content-Type": "application/json"},
                "body": body,
                "oidc_token": {
                    "service_account_email": self._service_account_email,
                    "audience": self._worker_base_url,
                },
            },
        }

        try:
            await asyncio.to_thread(self._client.create_task, request={"parent": parent, "task": task})
        except Exception as exc:
            try:
                from google.api_core.exceptions import (
                    AlreadyExists,  # type: ignore[import-not-found]
                )
            except ImportError:  # pragma: no cover - only local dependency-limited environment
                AlreadyExists = ()  # type: ignore[assignment,misc]
            if AlreadyExists and isinstance(exc, AlreadyExists):
                return task_name
            raise
        return task_name
