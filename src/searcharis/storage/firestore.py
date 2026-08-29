from __future__ import annotations

from typing import Any

from searcharis.models import DeploymentEvent, EvidenceRecord, IncidentRecord, RunRecord


def _firestore_modules():
    try:
        from google.cloud import firestore  # type: ignore[import-not-found]
        from google.cloud.firestore_v1.async_client import AsyncClient  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised in deployed environment
        raise RuntimeError("google-cloud-firestore is required for FirestoreStateStore") from exc
    return firestore, AsyncClient


class FirestoreStateStore:
    def __init__(self, project_id: str | None = None) -> None:
        _, async_client = _firestore_modules()
        self._client = async_client(project=project_id)

    async def _set_model(self, collection: str, document_id: str, model: Any) -> None:
        await self._client.collection(collection).document(document_id).set(
            model.model_dump(mode="json")
        )

    async def _get_model(self, collection: str, document_id: str, model_type: Any) -> Any | None:
        snapshot = await self._client.collection(collection).document(document_id).get()
        if not snapshot.exists:
            return None
        return model_type.model_validate(snapshot.to_dict())

    async def put_event(self, event: DeploymentEvent) -> None:
        await self._set_model("events", event.event_id, event)

    async def get_event(self, event_id: str) -> DeploymentEvent | None:
        return await self._get_model("events", event_id, DeploymentEvent)

    async def create_run(self, run: RunRecord) -> None:
        ref = self._client.collection("runs").document(run.run_id)
        if (await ref.get()).exists:
            raise ValueError(f"run already exists: {run.run_id}")
        await ref.set(run.model_dump(mode="json"))

    async def update_run(self, run: RunRecord) -> None:
        await self._set_model("runs", run.run_id, run)

    async def get_run(self, run_id: str) -> RunRecord | None:
        return await self._get_model("runs", run_id, RunRecord)

    async def save_evidence(self, evidence: EvidenceRecord) -> None:
        await self._set_model("evidence", evidence.evidence_id, evidence)

    async def list_evidence(self, run_id: str) -> list[EvidenceRecord]:
        query = self._client.collection("evidence").where("run_id", "==", run_id)
        return [EvidenceRecord.model_validate(doc.to_dict()) async for doc in query.stream()]

    async def get_incident(self, incident_id: str) -> IncidentRecord | None:
        return await self._get_model("incidents", incident_id, IncidentRecord)

    async def upsert_incident(self, incident: IncidentRecord) -> None:
        await self._set_model("incidents", incident.incident_id, incident)

    async def list_incidents(self) -> list[IncidentRecord]:
        return [
            IncidentRecord.model_validate(doc.to_dict())
            async for doc in self._client.collection("incidents").stream()
        ]

    async def claim_action(self, idempotency_key: str) -> bool:
        firestore, _ = _firestore_modules()
        ref = self._client.collection("actions").document(idempotency_key)
        transaction = self._client.transaction()

        @firestore.async_transactional
        async def claim(txn):
            snapshot = await ref.get(transaction=txn)
            if snapshot.exists:
                return False
            txn.set(ref, {"status": "claimed"})
            return True

        return bool(await claim(transaction))

    async def complete_action(self, idempotency_key: str, result: dict[str, object]) -> None:
        ref = self._client.collection("actions").document(idempotency_key)
        snapshot = await ref.get()
        if not snapshot.exists:
            raise KeyError(f"action was not claimed: {idempotency_key}")
        await ref.set({"status": "completed", "result": result}, merge=True)

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
