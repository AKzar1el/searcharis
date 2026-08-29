import asyncio
from datetime import UTC, datetime

import pytest

from searcharis.integrations.github import GitHubIssue
from searcharis.models import (
    DecisionClassification,
    DeploymentEvent,
    DiagnosisDecision,
    EvidenceRecord,
    IncidentRecord,
    ProposedAction,
    Severity,
    WorkflowState,
)
from searcharis.services.orchestrator import Orchestrator
from searcharis.storage.memory import InMemoryStateStore


def audit_evidence(run_id: str, *, broken: bool) -> list[EvidenceRecord]:
    now = datetime.now(UTC)
    result = [
        EvidenceRecord(
            evidence_id=f"{run_id}-complete",
            run_id=run_id,
            provider="digestseo-web-validator",
            tool="audit_public_webpage",
            target_url="https://demo.example/",
            finding_code="validator.audit_complete",
            category="Audit",
            severity="info",
            message="audit complete",
            retrieved_at=now,
            result_hash="b" * 64,
        )
    ]
    if broken:
        result.append(
            EvidenceRecord(
                evidence_id=f"{run_id}-title",
                run_id=run_id,
                provider="digestseo-web-validator",
                tool="audit_public_webpage",
                target_url="https://demo.example/",
                finding_code="seo.missing_title",
                category="SEO",
                severity="error",
                message="Missing or empty <title> tag.",
                retrieved_at=now,
                result_hash="b" * 64,
            )
        )
    return result


class FakeValidator:
    def __init__(self, broken_sequence):
        self._sequence = list(broken_sequence)

    async def audit_page(self, url: str, run_id: str):
        return audit_evidence(run_id, broken=self._sequence.pop(0))


class FakeDiagnostician:
    def __init__(self, decisions):
        self._decisions = list(decisions)

    async def diagnose(self, event, evidence, incident):
        decision = self._decisions.pop(0)
        ids = [item.evidence_id for item in evidence]
        if decision.proposed_action == ProposedAction.OPEN_INCIDENT:
            decision = decision.model_copy(update={"evidence_ids": [ids[-1]]})
        else:
            decision = decision.model_copy(update={"evidence_ids": [ids[0]]})
        return decision


class FakeBroker:
    def __init__(self):
        self.opens = []
        self.comments = []
        self.closes = []

    async def open_incident(self, repository, title, body):
        self.opens.append((repository, title, body))
        return GitHubIssue(number=42, html_url="https://github.com/AKzar1el/demo/issues/42")

    async def comment_incident(self, repository, issue_number, body):
        self.comments.append((repository, issue_number, body))

    async def close_incident(self, repository, issue_number):
        self.closes.append((repository, issue_number))


class FakeScheduler:
    def __init__(self):
        self.calls = []

    async def schedule(self, event_id, incident_id, delay_seconds):
        self.calls.append((event_id, incident_id, delay_seconds))
        return f"tasks/{event_id}-{incident_id}"


def regression_decision():
    return DiagnosisDecision(
        classification=DecisionClassification.REGRESSION,
        severity=Severity.HIGH,
        finding_codes=["seo.missing_title"],
        affected_urls=["https://demo.example/"],
        evidence_ids=["placeholder"],
        proposed_action=ProposedAction.OPEN_INCIDENT,
        summary="Missing title after deployment.",
        reasoning_summary="Fresh validator evidence reports the regression.",
    )


def recovery_decision():
    return DiagnosisDecision(
        classification=DecisionClassification.RECOVERY,
        severity=Severity.LOW,
        finding_codes=[],
        affected_urls=["https://demo.example/"],
        evidence_ids=["placeholder"],
        proposed_action=ProposedAction.CLOSE_INCIDENT,
        summary="Regression is no longer present.",
        reasoning_summary="Fresh completed audit no longer reports the triggering finding.",
    )


def event():
    return DeploymentEvent(
        event_id="evt-1",
        repository="AKzar1el/searcharis-demo",
        target_url="https://demo.example/",
        commit_sha="1234567",
        source="demo",
    )


@pytest.mark.asyncio
async def test_broken_deployment_opens_exactly_one_issue_and_schedules_verification():
    store = InMemoryStateStore()
    broker = FakeBroker()
    scheduler = FakeScheduler()
    orchestrator = Orchestrator(
        store=store,
        validator=FakeValidator([True]),
        diagnostician=FakeDiagnostician([regression_decision()]),
        github=broker,
        scheduler=scheduler,
    )

    run = await orchestrator.process_deployment(event())

    incidents = await store.list_incidents()
    assert len(broker.opens) == 1
    assert len(incidents) == 1
    assert incidents[0].github_issue_number == 42
    assert incidents[0].state == WorkflowState.VERIFYING
    assert run.state == WorkflowState.VERIFYING
    assert len(scheduler.calls) == 1


@pytest.mark.asyncio
async def test_duplicate_delivery_does_not_open_duplicate_issue():
    store = InMemoryStateStore()
    broker = FakeBroker()
    orchestrator = Orchestrator(
        store=store,
        validator=FakeValidator([True, True]),
        diagnostician=FakeDiagnostician([regression_decision(), regression_decision()]),
        github=broker,
        scheduler=FakeScheduler(),
    )

    await orchestrator.process_deployment(event())
    await orchestrator.process_deployment(event())

    assert len(broker.opens) == 1


@pytest.mark.asyncio
async def test_verification_cannot_close_while_triggering_finding_remains():
    store = InMemoryStateStore()
    current_event = event()
    await store.put_event(current_event)
    incident = IncidentRecord(
        incident_id="inc-1",
        fingerprint="f" * 64,
        repository=current_event.repository,
        target_origin="https://demo.example",
        affected_url=current_event.target_url,
        finding_code="seo.missing_title",
        state=WorkflowState.VERIFYING,
        github_issue_number=42,
        github_issue_url="https://github.com/AKzar1el/demo/issues/42",
    )
    await store.upsert_incident(incident)
    broker = FakeBroker()
    orchestrator = Orchestrator(
        store=store,
        validator=FakeValidator([True]),
        diagnostician=FakeDiagnostician([recovery_decision()]),
        github=broker,
        scheduler=FakeScheduler(),
    )

    run = await orchestrator.verify(current_event.event_id, incident.incident_id)

    assert broker.closes == []
    assert run.policy_action is not None
    assert run.policy_action.reason_code == "verification_failed"
    assert (await store.get_incident(incident.incident_id)).state == WorkflowState.VERIFYING


@pytest.mark.asyncio
async def test_fresh_clean_audit_comments_then_closes_and_resolves_incident():
    store = InMemoryStateStore()
    current_event = event()
    await store.put_event(current_event)
    incident = IncidentRecord(
        incident_id="inc-1",
        fingerprint="f" * 64,
        repository=current_event.repository,
        target_origin="https://demo.example",
        affected_url=current_event.target_url,
        finding_code="seo.missing_title",
        state=WorkflowState.VERIFYING,
        github_issue_number=42,
        github_issue_url="https://github.com/AKzar1el/demo/issues/42",
    )
    await store.upsert_incident(incident)
    broker = FakeBroker()
    orchestrator = Orchestrator(
        store=store,
        validator=FakeValidator([False]),
        diagnostician=FakeDiagnostician([recovery_decision()]),
        github=broker,
        scheduler=FakeScheduler(),
    )

    run = await orchestrator.verify(current_event.event_id, incident.incident_id)

    assert len(broker.comments) == 1
    assert broker.closes == [(current_event.repository, 42)]
    assert run.state == WorkflowState.RESOLVED
    assert (await store.get_incident(incident.incident_id)).state == WorkflowState.RESOLVED


@pytest.mark.asyncio
@pytest.mark.stress
async def test_concurrent_duplicate_deliveries_open_only_one_incident():
    count = 200
    store = InMemoryStateStore()
    broker = FakeBroker()
    scheduler = FakeScheduler()
    orchestrator = Orchestrator(
        store=store,
        validator=FakeValidator([True] * count),
        diagnostician=FakeDiagnostician([regression_decision() for _ in range(count)]),
        github=broker,
        scheduler=scheduler,
    )

    await asyncio.gather(*(orchestrator.process_deployment(event()) for _ in range(count)))

    assert len(broker.opens) == 1
    assert len(scheduler.calls) == 1
    assert len(await store.list_incidents()) == 1


@pytest.mark.asyncio
@pytest.mark.stress
async def test_concurrent_recovery_verifications_close_only_once():
    count = 200
    store = InMemoryStateStore()
    current_event = event()
    await store.put_event(current_event)
    incident = IncidentRecord(
        incident_id="inc-stress",
        fingerprint="f" * 64,
        repository=current_event.repository,
        target_origin="https://demo.example",
        affected_url=current_event.target_url,
        finding_code="seo.missing_title",
        state=WorkflowState.VERIFYING,
        github_issue_number=42,
        github_issue_url="https://github.com/AKzar1el/demo/issues/42",
    )
    await store.upsert_incident(incident)
    broker = FakeBroker()
    orchestrator = Orchestrator(
        store=store,
        validator=FakeValidator([False] * count),
        diagnostician=FakeDiagnostician([recovery_decision() for _ in range(count)]),
        github=broker,
        scheduler=FakeScheduler(),
    )

    await asyncio.gather(
        *(orchestrator.verify(current_event.event_id, incident.incident_id) for _ in range(count))
    )

    assert len(broker.comments) == 1
    assert len(broker.closes) == 1
    assert (await store.get_incident(incident.incident_id)).state == WorkflowState.RESOLVED


class FlakyOpenBroker(FakeBroker):
    def __init__(self):
        super().__init__()
        self.open_attempts = 0

    async def open_incident(self, repository, title, body):
        self.open_attempts += 1
        if self.open_attempts == 1:
            raise RuntimeError("simulated GitHub outage")
        return await super().open_incident(repository, title, body)


class FlakyCloseBroker(FakeBroker):
    def __init__(self):
        super().__init__()
        self.close_attempts = 0

    async def close_incident(self, repository, issue_number):
        self.close_attempts += 1
        if self.close_attempts == 1:
            raise RuntimeError("simulated GitHub outage")
        await super().close_incident(repository, issue_number)


class FlakyScheduler(FakeScheduler):
    def __init__(self):
        super().__init__()
        self.attempts = 0

    async def schedule(self, event_id, incident_id, delay_seconds):
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("simulated Cloud Tasks outage")
        return await super().schedule(event_id, incident_id, delay_seconds)


@pytest.mark.asyncio
async def test_failed_github_open_is_retryable_and_retry_can_open_incident():
    store = InMemoryStateStore()
    broker = FlakyOpenBroker()
    orchestrator = Orchestrator(
        store=store,
        validator=FakeValidator([True, True]),
        diagnostician=FakeDiagnostician([regression_decision(), regression_decision()]),
        github=broker,
        scheduler=FakeScheduler(),
    )

    first = await orchestrator.process_deployment(event())
    second = await orchestrator.process_deployment(event())

    assert first.state == WorkflowState.FAILED_RETRYABLE
    assert second.state == WorkflowState.VERIFYING
    assert broker.open_attempts == 2
    assert len(broker.opens) == 1
    assert len(await store.list_incidents()) == 1


@pytest.mark.asyncio
async def test_failed_verification_schedule_is_retryable_without_duplicate_issue():
    store = InMemoryStateStore()
    broker = FakeBroker()
    scheduler = FlakyScheduler()
    orchestrator = Orchestrator(
        store=store,
        validator=FakeValidator([True, True]),
        diagnostician=FakeDiagnostician([regression_decision(), regression_decision()]),
        github=broker,
        scheduler=scheduler,
    )

    first = await orchestrator.process_deployment(event())
    second = await orchestrator.process_deployment(event())

    assert first.state == WorkflowState.FAILED_RETRYABLE
    assert second.state == WorkflowState.VERIFYING
    assert len(broker.opens) == 1
    assert scheduler.attempts == 2
    assert len(scheduler.calls) == 1


@pytest.mark.asyncio
async def test_failed_github_close_is_retryable_without_duplicate_comment():
    store = InMemoryStateStore()
    current_event = event()
    await store.put_event(current_event)
    incident = IncidentRecord(
        incident_id="inc-close-retry",
        fingerprint="f" * 64,
        repository=current_event.repository,
        target_origin="https://demo.example",
        affected_url=current_event.target_url,
        finding_code="seo.missing_title",
        state=WorkflowState.VERIFYING,
        github_issue_number=42,
        github_issue_url="https://github.com/AKzar1el/demo/issues/42",
    )
    await store.upsert_incident(incident)
    broker = FlakyCloseBroker()
    orchestrator = Orchestrator(
        store=store,
        validator=FakeValidator([False, False]),
        diagnostician=FakeDiagnostician([recovery_decision(), recovery_decision()]),
        github=broker,
        scheduler=FakeScheduler(),
    )

    first = await orchestrator.verify(current_event.event_id, incident.incident_id)
    second = await orchestrator.verify(current_event.event_id, incident.incident_id)

    assert first.state == WorkflowState.FAILED_RETRYABLE
    assert second.state == WorkflowState.RESOLVED
    assert broker.close_attempts == 2
    assert len(broker.comments) == 1
    assert len(broker.closes) == 1
