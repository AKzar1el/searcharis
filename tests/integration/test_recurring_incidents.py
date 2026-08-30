from datetime import UTC, datetime

import pytest

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


def _evidence(run_id: str, *, broken: bool) -> list[EvidenceRecord]:
    now = datetime.now(UTC)
    records = [
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
            result_hash="a" * 64,
        )
    ]
    if broken:
        records.append(
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
    return records


class SequenceValidator:
    def __init__(self, broken_sequence: list[bool]) -> None:
        self._sequence = list(broken_sequence)

    async def audit_page(self, url: str, run_id: str) -> list[EvidenceRecord]:
        return _evidence(run_id, broken=self._sequence.pop(0))


class SequenceDiagnostician:
    def __init__(self, decisions: list[DiagnosisDecision]) -> None:
        self._decisions = list(decisions)

    async def diagnose(self, event, evidence, incident):
        decision = self._decisions.pop(0)
        evidence_ids = [item.evidence_id for item in evidence]
        selected = evidence_ids[-1] if decision.proposed_action == ProposedAction.OPEN_INCIDENT else evidence_ids[0]
        return decision.model_copy(update={"evidence_ids": [selected]})


class RecordingBroker:
    def __init__(self) -> None:
        self.opens: list[tuple[str, str, str, GitHubIssue]] = []
        self.comments: list[tuple[str, int, str]] = []
        self.closes: list[tuple[str, int]] = []

    async def open_incident(self, repository: str, title: str, body: str) -> GitHubIssue:
        number = 100 + len(self.opens) + 1
        issue = GitHubIssue(
            number=number,
            html_url=f"https://github.com/AKzar1el/demo/issues/{number}",
        )
        self.opens.append((repository, title, body, issue))
        return issue

    async def find_issue_by_marker(self, repository: str, marker: str):
        for repo, _, body, issue in self.opens:
            if repo == repository and marker in body:
                return issue
        return None

    async def comment_incident(self, repository: str, issue_number: int, body: str) -> None:
        self.comments.append((repository, issue_number, body))

    async def comment_exists(self, repository: str, issue_number: int, marker: str) -> bool:
        return any(
            repo == repository and number == issue_number and marker in body
            for repo, number, body in self.comments
        )

    async def close_incident(self, repository: str, issue_number: int) -> None:
        self.closes.append((repository, issue_number))

    async def get_issue_state(self, repository: str, issue_number: int) -> str:
        return "closed" if (repository, issue_number) in self.closes else "open"


class RecordingScheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def schedule(self, event_id: str, incident_id: str, delay_seconds: int) -> str:
        self.calls.append((event_id, incident_id, delay_seconds))
        return f"tasks/{event_id}-{incident_id}"


def _regression_decision() -> DiagnosisDecision:
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


def _recovery_decision() -> DiagnosisDecision:
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


def _event(event_id: str, commit_sha: str) -> DeploymentEvent:
    return DeploymentEvent(
        event_id=event_id,
        repository="AKzar1el/searcharis-demo",
        target_url="https://demo.example/",
        commit_sha=commit_sha,
        source="demo",
    )


@pytest.mark.asyncio
async def test_same_regression_after_resolution_creates_new_incident_occurrence() -> None:
    store = InMemoryStateStore()
    broker = RecordingBroker()
    scheduler = RecordingScheduler()
    orchestrator = Orchestrator(
        store=store,
        validator=SequenceValidator([True, False, True]),
        diagnostician=SequenceDiagnostician(
            [_regression_decision(), _recovery_decision(), _regression_decision()]
        ),
        github=broker,
        scheduler=scheduler,
    )

    first_event = _event("evt-first", "1111111")
    first_run = await orchestrator.process_deployment(first_event)
    first_incident = await store.get_incident(first_run.incident_id)
    assert first_incident is not None

    recovery_run = await orchestrator.verify(first_event.event_id, first_incident.incident_id)
    assert recovery_run.state == WorkflowState.RESOLVED

    second_event = _event("evt-second", "2222222")
    second_run = await orchestrator.process_deployment(second_event)
    second_incident = await store.get_incident(second_run.incident_id)
    assert second_incident is not None

    incidents = await store.list_incidents()
    assert len(broker.opens) == 2
    assert len(incidents) == 2
    assert first_incident.incident_id != second_incident.incident_id
    assert first_incident.fingerprint == second_incident.fingerprint
    assert (await store.get_incident(first_incident.incident_id)).state == WorkflowState.RESOLVED
    assert second_incident.state == WorkflowState.VERIFYING
    assert second_incident.github_issue_number == 102
