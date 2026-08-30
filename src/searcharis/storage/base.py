from __future__ import annotations

from typing import Protocol

from searcharis.models import (
    ActionClaim,
    DeploymentEvent,
    EvidenceRecord,
    IncidentRecord,
    RunRecord,
)


class StateStore(Protocol):
    async def put_event(self, event: DeploymentEvent) -> None: ...

    async def get_event(self, event_id: str) -> DeploymentEvent | None: ...

    async def create_run(self, run: RunRecord) -> None: ...

    async def update_run(self, run: RunRecord) -> None: ...

    async def get_run(self, run_id: str) -> RunRecord | None: ...

    async def save_evidence(self, evidence: EvidenceRecord) -> None: ...

    async def list_evidence(self, run_id: str) -> list[EvidenceRecord]: ...

    async def get_incident(self, incident_id: str) -> IncidentRecord | None: ...

    async def upsert_incident(self, incident: IncidentRecord) -> None: ...

    async def find_active_incident(
        self, repository: str, affected_url: str
    ) -> IncidentRecord | None: ...

    async def list_incidents(self, limit: int = 100) -> list[IncidentRecord]: ...

    async def claim_action(
        self,
        idempotency_key: str,
        *,
        operation: str = "legacy",
        incident_id: str = "legacy",
        marker: str = "",
        lease_seconds: int = 120,
    ) -> ActionClaim: ...

    async def complete_action(self, idempotency_key: str, result: dict[str, object]) -> None: ...

    async def release_action(self, idempotency_key: str) -> None: ...
