from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest

from searcharis.ids import action_key, incident_fingerprint
from searcharis.integrations.github import GitHubIssue
from searcharis.models import (
    DecisionClassification,
    DeploymentEvent,
    DiagnosisDecision,
    EvidenceRecord,
    ProposedAction,
    Severity,
    WorkflowState,
)
from searcharis.services.orchestrator import Orchestrator
from searcharis.storage.memory import InMemoryStateStore


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class BrokenValidator:
    async def audit_page(self, url: str, run_id: str):
        return [
            EvidenceRecord(
                evidence_id=f"{run_id}-complete",
                run_id=run_id,
                provider="digestseo-web-validator",
                tool="audit_public_webpage",
                target_url=url,
                finding_code="validator.audit_complete",
                category="Audit",
                severity="info",
                message="audit complete",
                result_hash="a" * 64,
            ),
            EvidenceRecord(
                evidence_id=f"{run_id}-title",
                run_id=run_id,
                provider="digestseo-web-validator",
                tool="audit_public_webpage",
                target_url=url,
                finding_code="seo.missing_title",
                category="SEO",
                severity="error",
                message="Missing or empty <title> tag.",
                result_hash="b" * 64,
            ),
        ]


class RegressionDiagnostician:
    async def diagnose(self, event, evidence, incident):
        finding = next(item for item in evidence if item.finding_code == "seo.missing_title")
        return DiagnosisDecision(
            classification=DecisionClassification.REGRESSION,
            severity=Severity.HIGH,
            finding_codes=[finding.finding_code],
            affected_urls=[event.target_url],
            evidence_ids=[finding.evidence_id],
            proposed_action=ProposedAction.OPEN_INCIDENT,
            summary="Title missing after deployment.",
            reasoning_summary="Fresh validator evidence reports the regression.",
        )


class ReconciliationBroker:
    def __init__(self, recovered_issue: GitHubIssue) -> None:
        self.recovered_issue = recovered_issue
        self.open_calls = 0
        self.find_calls: list[tuple[str, str]] = []

    async def find_issue_by_marker(self, repository: str, marker: str):
        self.find_calls.append((repository, marker))
        return self.recovered_issue

    async def open_incident(self, repository: str, title: str, body: str):
        self.open_calls += 1
        raise AssertionError("stale reconciliation must not create a duplicate issue")


class FlakyReconciliationBroker(ReconciliationBroker):
    def __init__(self, recovered_issue: GitHubIssue) -> None:
        super().__init__(recovered_issue)
        self.fail_next_find = True

    async def find_issue_by_marker(self, repository: str, marker: str):
        self.find_calls.append((repository, marker))
        if self.fail_next_find:
            self.fail_next_find = False
            raise RuntimeError("temporary GitHub read failure")
        return self.recovered_issue


class AmbiguousOpenBroker:
    def __init__(self, recovered_issue: GitHubIssue) -> None:
        self.recovered_issue = recovered_issue
        self.open_calls = 0
        self.find_calls: list[tuple[str, str]] = []
        self.remote_issue_visible = False

    async def open_incident(self, repository: str, title: str, body: str):
        self.open_calls += 1
        self.remote_issue_visible = True
        raise RuntimeError("connection reset after GitHub accepted the issue")

    async def find_issue_by_marker(self, repository: str, marker: str):
        self.find_calls.append((repository, marker))
        return self.recovered_issue if self.remote_issue_visible else None


class Scheduler:
    def __init__(self) -> None:
        self.calls = []

    async def schedule(self, event_id, incident_id, delay_seconds):
        self.calls.append((event_id, incident_id, delay_seconds))
        return f"tasks/{event_id}-{incident_id}"


def _event() -> DeploymentEvent:
    return DeploymentEvent(
        event_id="evt-reconcile-open",
        repository="AKzar1el/searcharis",
        target_url="https://demo.example/",
        commit_sha="1234567",
        source="demo",
    )


def _open_action(event: DeploymentEvent) -> tuple[str, str, str]:
    target_url = str(event.target_url)
    split = urlsplit(target_url)
    incident_id = incident_fingerprint(
        event.repository,
        f"{split.scheme}://{split.netloc}",
        target_url,
        "seo.missing_title",
    )
    key = action_key("open", incident_id, "b" * 64)
    marker = f"<!-- searcharis-action:{key} -->"
    return incident_id, key, marker


@pytest.mark.asyncio
async def test_stale_open_claim_reconciles_existing_github_issue_after_process_crash():
    clock = MutableClock(datetime(2026, 8, 30, 12, 0, tzinfo=UTC))
    store = InMemoryStateStore(clock=clock)
    event = _event()
    incident_id, key, marker = _open_action(event)

    await store.claim_action(
        key,
        operation="open",
        incident_id=incident_id,
        marker=marker,
        lease_seconds=120,
    )
    clock.advance(121)

    broker = ReconciliationBroker(
        GitHubIssue(number=77, html_url="https://github.com/AKzar1el/searcharis/issues/77")
    )
    scheduler = Scheduler()
    orchestrator = Orchestrator(
        store=store,
        validator=BrokenValidator(),
        diagnostician=RegressionDiagnostician(),
        github=broker,
        scheduler=scheduler,
    )

    run = await orchestrator.process_deployment(event)

    incident = await store.get_incident(incident_id)
    assert broker.open_calls == 0
    assert broker.find_calls == [(event.repository, marker)]
    assert incident is not None
    assert incident.github_issue_number == 77
    assert incident.state == WorkflowState.VERIFYING
    assert run.state == WorkflowState.VERIFYING
    assert len(scheduler.calls) == 1


@pytest.mark.asyncio
async def test_reconciliation_read_failure_keeps_lease_until_safe_stale_retry():
    clock = MutableClock(datetime(2026, 8, 30, 12, 0, tzinfo=UTC))
    store = InMemoryStateStore(clock=clock)
    event = _event()
    incident_id, key, marker = _open_action(event)

    await store.claim_action(
        key,
        operation="open",
        incident_id=incident_id,
        marker=marker,
        lease_seconds=120,
    )
    clock.advance(121)

    broker = FlakyReconciliationBroker(
        GitHubIssue(number=78, html_url="https://github.com/AKzar1el/searcharis/issues/78")
    )
    scheduler = Scheduler()
    orchestrator = Orchestrator(
        store=store,
        validator=BrokenValidator(),
        diagnostician=RegressionDiagnostician(),
        github=broker,
        scheduler=scheduler,
    )

    first = await orchestrator.process_deployment(event)
    immediate_retry = await orchestrator.process_deployment(event)

    assert first.state == WorkflowState.FAILED_RETRYABLE
    assert immediate_retry.state == WorkflowState.FAILED_RETRYABLE
    assert broker.open_calls == 0
    assert broker.find_calls == [(event.repository, marker)]
    assert await store.get_incident(incident_id) is None

    clock.advance(121)
    recovered = await orchestrator.process_deployment(event)

    incident = await store.get_incident(incident_id)
    assert recovered.state == WorkflowState.VERIFYING
    assert broker.open_calls == 0
    assert broker.find_calls == [
        (event.repository, marker),
        (event.repository, marker),
    ]
    assert incident is not None
    assert incident.github_issue_number == 78
    assert incident.state == WorkflowState.VERIFYING
    assert len(scheduler.calls) == 1


@pytest.mark.asyncio
async def test_ambiguous_open_failure_keeps_fresh_lease_until_reconciliation():
    clock = MutableClock(datetime(2026, 8, 30, 12, 0, tzinfo=UTC))
    store = InMemoryStateStore(clock=clock)
    event = _event()
    incident_id, _, marker = _open_action(event)
    broker = AmbiguousOpenBroker(
        GitHubIssue(number=79, html_url="https://github.com/AKzar1el/searcharis/issues/79")
    )
    scheduler = Scheduler()
    orchestrator = Orchestrator(
        store=store,
        validator=BrokenValidator(),
        diagnostician=RegressionDiagnostician(),
        github=broker,
        scheduler=scheduler,
    )

    first = await orchestrator.process_deployment(event)
    immediate_retry = await orchestrator.process_deployment(event)

    assert first.state == WorkflowState.FAILED_RETRYABLE
    assert immediate_retry.state == WorkflowState.FAILED_RETRYABLE
    assert broker.open_calls == 1
    assert broker.find_calls == []
    assert await store.get_incident(incident_id) is None

    clock.advance(121)
    recovered = await orchestrator.process_deployment(event)

    incident = await store.get_incident(incident_id)
    assert recovered.state == WorkflowState.VERIFYING
    assert broker.open_calls == 1
    assert broker.find_calls == [(event.repository, marker)]
    assert incident is not None
    assert incident.github_issue_number == 79
    assert incident.state == WorkflowState.VERIFYING
    assert len(scheduler.calls) == 1
