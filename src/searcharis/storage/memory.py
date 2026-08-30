from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from searcharis.models import (
    ActionClaim,
    DeploymentEvent,
    EvidenceRecord,
    IncidentRecord,
    RunRecord,
    WorkflowState,
)


class InMemoryStateStore:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._events: dict[str, DeploymentEvent] = {}
        self._runs: dict[str, RunRecord] = {}
        self._evidence: dict[str, EvidenceRecord] = {}
        self._incidents: dict[str, IncidentRecord] = {}
        self._actions: dict[str, dict[str, object]] = {}
        self._action_lock = asyncio.Lock()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def put_event(self, event: DeploymentEvent) -> None:
        self._events[event.event_id] = event.model_copy(deep=True)

    async def get_event(self, event_id: str) -> DeploymentEvent | None:
        value = self._events.get(event_id)
        return value.model_copy(deep=True) if value else None

    async def create_run(self, run: RunRecord) -> None:
        if run.run_id in self._runs:
            raise ValueError(f"run already exists: {run.run_id}")
        self._runs[run.run_id] = run.model_copy(deep=True)

    async def update_run(self, run: RunRecord) -> None:
        if run.run_id not in self._runs:
            raise KeyError(f"unknown run: {run.run_id}")
        self._runs[run.run_id] = run.model_copy(deep=True)

    async def get_run(self, run_id: str) -> RunRecord | None:
        value = self._runs.get(run_id)
        return value.model_copy(deep=True) if value else None

    async def save_evidence(self, evidence: EvidenceRecord) -> None:
        self._evidence[evidence.evidence_id] = evidence.model_copy(deep=True)

    async def list_evidence(self, run_id: str) -> list[EvidenceRecord]:
        return [
            value.model_copy(deep=True)
            for value in self._evidence.values()
            if value.run_id == run_id
        ]

    async def get_incident(self, incident_id: str) -> IncidentRecord | None:
        value = self._incidents.get(incident_id)
        return value.model_copy(deep=True) if value else None

    async def upsert_incident(self, incident: IncidentRecord) -> None:
        self._incidents[incident.incident_id] = incident.model_copy(deep=True)

    async def find_active_incident(
        self,
        repository: str,
        affected_url: str,
    ) -> IncidentRecord | None:
        candidates = sorted(
            self._incidents.values(),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        for incident in candidates:
            if (
                incident.repository == repository
                and str(incident.affected_url) == affected_url
                and incident.state != WorkflowState.RESOLVED
            ):
                return incident.model_copy(deep=True)
        return None

    async def list_incidents(self, limit: int = 100) -> list[IncidentRecord]:
        bounded_limit = max(1, min(int(limit), 500))
        incidents = sorted(
            self._incidents.values(),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        return [value.model_copy(deep=True) for value in incidents[:bounded_limit]]

    async def claim_action(
        self,
        idempotency_key: str,
        *,
        operation: str = "legacy",
        incident_id: str = "legacy",
        marker: str = "",
        lease_seconds: int = 120,
    ) -> ActionClaim:
        async with self._action_lock:
            now = self._clock()
            current = self._actions.get(idempotency_key)
            if current is None:
                self._actions[idempotency_key] = {
                    "status": "claimed",
                    "claimed_at": now,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "operation": operation,
                    "incident_id": incident_id,
                    "marker": marker,
                    "stale_takeover": False,
                }
                return ActionClaim(acquired=True)

            if current.get("status") == "completed":
                result = current.get("result")
                return ActionClaim(
                    acquired=False,
                    completed=True,
                    result=dict(result) if isinstance(result, dict) else None,
                )

            lease_expires_at = current.get("lease_expires_at")
            if isinstance(lease_expires_at, datetime) and lease_expires_at <= now:
                current.update(
                    {
                        "status": "claimed",
                        "claimed_at": now,
                        "lease_expires_at": now + timedelta(seconds=lease_seconds),
                        "operation": operation,
                        "incident_id": incident_id,
                        "marker": marker,
                        "stale_takeover": True,
                    }
                )
                return ActionClaim(acquired=True, stale_takeover=True)

            return ActionClaim(acquired=False)

    async def complete_action(self, idempotency_key: str, result: dict[str, object]) -> None:
        async with self._action_lock:
            if idempotency_key not in self._actions:
                raise KeyError(f"action was not claimed: {idempotency_key}")
            self._actions[idempotency_key] = {
                **self._actions[idempotency_key],
                "status": "completed",
                "result": dict(result),
            }

    async def release_action(self, idempotency_key: str) -> None:
        async with self._action_lock:
            current = self._actions.get(idempotency_key)
            if (
                current
                and current.get("status") == "claimed"
                and current.get("stale_takeover") is not True
            ):
                self._actions.pop(idempotency_key, None)
