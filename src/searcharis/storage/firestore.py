from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import AnyUrl

from searcharis.models import (
    ActionClaim,
    DeploymentEvent,
    EvidenceRecord,
    IncidentRecord,
    RunRecord,
    WorkflowState,
)

_OPERATIONAL_TTL = timedelta(days=30)


def _firestore_modules():
    try:
        from google.cloud import firestore  # type: ignore[import-not-found]
        from google.cloud.firestore_v1.async_client import (
            AsyncClient,  # type: ignore[import-not-found]
        )
    except ImportError as exc:  # pragma: no cover - exercised in deployed environment
        raise RuntimeError("google-cloud-firestore is required for FirestoreStateStore") from exc
    return firestore, AsyncClient


def _normalize_firestore_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _normalize_firestore_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_firestore_value(item) for item in value]
    if isinstance(value, (str, int, float, bool, bytes, type(None))):
        return value
    if isinstance(value, AnyUrl):
        return str(value)
    if value.__class__.__module__.startswith("pydantic_core") and value.__class__.__name__ in {
        "Url",
        "MultiHostUrl",
    }:
        return str(value)
    return value


def _model_payload(model: Any, *, expires_at: datetime | None = None) -> dict[str, Any]:
    payload = _normalize_firestore_value(model.model_dump(mode="python"))
    if not isinstance(payload, dict):
        raise TypeError("Firestore model payload must be a mapping")
    if expires_at is not None:
        payload["expires_at"] = expires_at
    return payload


def _operational_expiry(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) + _OPERATIONAL_TTL


class FirestoreStateStore:
    def __init__(
        self,
        project_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        _, async_client = _firestore_modules()
        self._client = async_client(project=project_id)
        self._clock = clock or (lambda: datetime.now(UTC))

    async def _set_model(
        self,
        collection: str,
        document_id: str,
        model: Any,
        *,
        operational: bool = False,
    ) -> None:
        expires_at = _operational_expiry() if operational else None
        await self._client.collection(collection).document(document_id).set(
            _model_payload(model, expires_at=expires_at)
        )

    async def _get_model(self, collection: str, document_id: str, model_type: Any) -> Any | None:
        snapshot = await self._client.collection(collection).document(document_id).get()
        if not snapshot.exists:
            return None
        return model_type.model_validate(snapshot.to_dict())

    async def put_event(self, event: DeploymentEvent) -> None:
        await self._set_model("events", event.event_id, event, operational=True)

    async def get_event(self, event_id: str) -> DeploymentEvent | None:
        return await self._get_model("events", event_id, DeploymentEvent)

    async def create_run(self, run: RunRecord) -> None:
        ref = self._client.collection("runs").document(run.run_id)
        if (await ref.get()).exists:
            raise ValueError(f"run already exists: {run.run_id}")
        await ref.set(_model_payload(run, expires_at=_operational_expiry()))

    async def update_run(self, run: RunRecord) -> None:
        await self._set_model("runs", run.run_id, run, operational=True)

    async def get_run(self, run_id: str) -> RunRecord | None:
        return await self._get_model("runs", run_id, RunRecord)

    async def save_evidence(self, evidence: EvidenceRecord) -> None:
        await self._set_model("evidence", evidence.evidence_id, evidence, operational=True)

    async def list_evidence(self, run_id: str) -> list[EvidenceRecord]:
        query = self._client.collection("evidence").where("run_id", "==", run_id)
        return [EvidenceRecord.model_validate(doc.to_dict()) async for doc in query.stream()]

    async def get_incident(self, incident_id: str) -> IncidentRecord | None:
        return await self._get_model("incidents", incident_id, IncidentRecord)

    async def upsert_incident(self, incident: IncidentRecord) -> None:
        await self._set_model("incidents", incident.incident_id, incident)

    async def find_active_incident(
        self,
        repository: str,
        affected_url: str,
    ) -> IncidentRecord | None:
        try:
            from google.cloud.firestore_v1.base_query import (
                FieldFilter,  # type: ignore[import-not-found]
            )
        except ImportError as exc:  # pragma: no cover - deployed dependency
            raise RuntimeError("google-cloud-firestore is required for FirestoreStateStore") from exc

        firestore, _ = _firestore_modules()
        query = self._client.collection("incidents")
        query = query.where(filter=FieldFilter("repository", "==", repository))
        query = query.where(filter=FieldFilter("affected_url", "==", affected_url))
        query = query.order_by("updated_at", direction=firestore.Query.DESCENDING).limit(10)
        async for doc in query.stream():
            incident = IncidentRecord.model_validate(doc.to_dict())
            if incident.state != WorkflowState.RESOLVED:
                return incident
        return None

    async def list_incidents(self, limit: int = 100) -> list[IncidentRecord]:
        bounded_limit = max(1, min(int(limit), 500))
        firestore, _ = _firestore_modules()
        query = (
            self._client.collection("incidents")
            .order_by("updated_at", direction=firestore.Query.DESCENDING)
            .limit(bounded_limit)
        )
        return [IncidentRecord.model_validate(doc.to_dict()) async for doc in query.stream()]

    async def claim_action(
        self,
        idempotency_key: str,
        *,
        operation: str = "legacy",
        incident_id: str = "legacy",
        marker: str = "",
        lease_seconds: int = 120,
    ) -> ActionClaim:
        firestore, _ = _firestore_modules()
        ref = self._client.collection("actions").document(idempotency_key)
        transaction = self._client.transaction()
        now = self._clock()
        lease_expires_at = now + timedelta(seconds=lease_seconds)

        @firestore.async_transactional
        async def claim(txn):
            snapshot = await ref.get(transaction=txn)
            data = snapshot.to_dict() or {} if snapshot.exists else {}
            if snapshot.exists and data.get("status") == "completed":
                result = data.get("result")
                return ActionClaim(
                    acquired=False,
                    completed=True,
                    result=dict(result) if isinstance(result, dict) else None,
                )

            if snapshot.exists:
                current_lease = data.get("lease_expires_at")
                if isinstance(current_lease, datetime) and current_lease > now:
                    return ActionClaim(acquired=False)
                stale_takeover = True
            else:
                stale_takeover = False

            txn.set(
                ref,
                {
                    "status": "claimed",
                    "claimed_at": now,
                    "lease_expires_at": lease_expires_at,
                    "operation": operation,
                    "incident_id": incident_id,
                    "marker": marker,
                    "expires_at": _operational_expiry(now),
                },
            )
            return ActionClaim(acquired=True, stale_takeover=stale_takeover)

        return await claim(transaction)

    async def complete_action(self, idempotency_key: str, result: dict[str, object]) -> None:
        ref = self._client.collection("actions").document(idempotency_key)
        snapshot = await ref.get()
        if not snapshot.exists:
            raise KeyError(f"action was not claimed: {idempotency_key}")
        await ref.set(
            {
                "status": "completed",
                "result": result,
                "expires_at": _operational_expiry(),
            },
            merge=True,
        )

    async def release_action(self, idempotency_key: str) -> None:
        firestore, _ = _firestore_modules()
        ref = self._client.collection("actions").document(idempotency_key)
        transaction = self._client.transaction()

        @firestore.async_transactional
        async def release(txn):
            snapshot = await ref.get(transaction=txn)
            if snapshot.exists and (snapshot.to_dict() or {}).get("status") == "claimed":
                txn.delete(ref)

        await release(transaction)
