from searcharis.models import DeploymentEvent, IncidentRecord, RunRecord, WorkflowState
from searcharis.storage.memory import InMemoryStateStore


async def test_claim_action_is_atomic_for_sequential_duplicate_calls():
    store = InMemoryStateStore()
    first = await store.claim_action("same")
    second = await store.claim_action("same")
    assert first.acquired is True
    assert second.acquired is False


async def test_event_run_and_incident_round_trip():
    store = InMemoryStateStore()
    event = DeploymentEvent(
        event_id="evt-1",
        repository="AKzar1el/searcharis-demo",
        target_url="https://demo.example/",
        commit_sha="1234567",
        source="demo",
    )
    run = RunRecord(run_id="run-1", event_id=event.event_id, state=WorkflowState.RECEIVED)
    incident = IncidentRecord(
        incident_id="inc-1",
        fingerprint="a" * 64,
        repository=event.repository,
        target_origin="https://demo.example",
        affected_url=event.target_url,
        finding_code="seo.missing_title",
    )

    await store.put_event(event)
    await store.create_run(run)
    await store.upsert_incident(incident)

    assert (await store.get_event("evt-1")) == event
    assert (await store.get_run("run-1")) == run
    assert (await store.get_incident("inc-1")) == incident
