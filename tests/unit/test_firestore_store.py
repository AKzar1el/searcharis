from datetime import UTC, datetime

import pytest

from searcharis.models import DeploymentEvent, IncidentRecord, RunRecord, WorkflowState
from searcharis.storage import firestore as firestore_module
from searcharis.storage.firestore import FirestoreStateStore
from searcharis.storage.memory import InMemoryStateStore


def _incident(number: int, state: WorkflowState) -> IncidentRecord:
    return IncidentRecord(
        incident_id=f"{'a' * 63}{number}",
        fingerprint=f"{'b' * 63}{number}",
        repository="AKzar1el/searcharis",
        target_origin="https://demo.example",
        affected_url="https://demo.example/",
        finding_code="seo.missing_title",
        state=state,
    )


def test_firestore_payload_keeps_datetimes_native_and_urls_as_strings():
    payload_builder = getattr(firestore_module, "_model_payload", None)
    assert callable(payload_builder), "Firestore model payload helper is missing"

    event = DeploymentEvent(
        event_id="evt-native",
        repository="AKzar1el/searcharis",
        target_url="https://demo.example/",
        commit_sha="1234567",
        source="demo",
    )

    payload = payload_builder(event)

    assert isinstance(payload["deployed_at"], datetime)
    assert payload["deployed_at"].tzinfo is not None
    assert payload["target_url"] == "https://demo.example/"


def test_operational_payload_adds_native_expiry_without_changing_model_schema():
    payload_builder = getattr(firestore_module, "_model_payload", None)
    assert callable(payload_builder), "Firestore model payload helper is missing"

    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    run = RunRecord(run_id="run-1", event_id="evt-1", state=WorkflowState.RECEIVED)

    payload = payload_builder(run, expires_at=now)

    assert payload["expires_at"] == now
    assert "expires_at" not in run.model_fields_set


@pytest.mark.asyncio
async def test_memory_store_finds_active_incident_without_unbounded_listing():
    store = InMemoryStateStore()
    resolved = _incident(1, WorkflowState.RESOLVED)
    active = _incident(2, WorkflowState.VERIFYING)
    await store.upsert_incident(resolved)
    await store.upsert_incident(active)

    finder = getattr(store, "find_active_incident", None)
    assert callable(finder), "bounded active incident lookup is missing"

    found = await finder("AKzar1el/searcharis", "https://demo.example/")

    assert found is not None
    assert found.incident_id == active.incident_id


@pytest.mark.asyncio
async def test_memory_incident_listing_is_bounded_and_newest_first():
    store = InMemoryStateStore()
    older = _incident(1, WorkflowState.RESOLVED).model_copy(
        update={"updated_at": datetime(2026, 8, 29, tzinfo=UTC)}
    )
    newer = _incident(2, WorkflowState.VERIFYING).model_copy(
        update={"updated_at": datetime(2026, 8, 30, tzinfo=UTC)}
    )
    await store.upsert_incident(older)
    await store.upsert_incident(newer)

    items = await store.list_incidents(limit=1)

    assert [item.incident_id for item in items] == [newer.incident_id]


class FakeSnapshot:
    def __init__(self, data):
        self._data = data
        self.exists = True

    def to_dict(self):
        return self._data


class FakeQuery:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.filters = []
        self.ordering = []
        self.limit_value = None

    def where(self, *args, **kwargs):
        self.filters.append((args, kwargs))
        return self

    def order_by(self, field, direction=None):
        self.ordering.append((field, direction))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    async def stream(self):
        for snapshot in self.snapshots:
            yield snapshot


class FakeClient:
    def __init__(self, query):
        self.query = query

    def collection(self, name):
        assert name == "incidents"
        return self.query


@pytest.mark.asyncio
async def test_firestore_list_incidents_orders_and_limits_query():
    query = FakeQuery([])
    store = FirestoreStateStore.__new__(FirestoreStateStore)
    store._client = FakeClient(query)

    items = await store.list_incidents(limit=25)

    assert items == []
    assert query.limit_value == 25
    assert query.ordering and query.ordering[0][0] == "updated_at"


@pytest.mark.asyncio
async def test_firestore_active_incident_lookup_is_bounded():
    active = _incident(2, WorkflowState.VERIFYING)
    query = FakeQuery([FakeSnapshot(active.model_dump(mode="json"))])
    store = FirestoreStateStore.__new__(FirestoreStateStore)
    store._client = FakeClient(query)

    finder = getattr(store, "find_active_incident", None)
    assert callable(finder), "bounded active incident lookup is missing"

    found = await finder("AKzar1el/searcharis", "https://demo.example/")

    assert found is not None
    assert found.incident_id == active.incident_id
    assert query.limit_value is not None
    assert query.limit_value <= 10
