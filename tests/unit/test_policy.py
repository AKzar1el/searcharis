from datetime import UTC, datetime, timedelta

from searcharis.models import (
    DecisionClassification,
    DiagnosisDecision,
    EvidenceRecord,
    IncidentRecord,
    ProposedAction,
    Severity,
    WorkflowState,
)
from searcharis.policy import evaluate_policy

NOW = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)


def evidence(
    evidence_id: str,
    finding_code: str,
    *,
    severity: str = "error",
    minutes_old: int = 0,
    run_id: str = "run-1",
):
    return EvidenceRecord(
        evidence_id=evidence_id,
        run_id=run_id,
        provider="digestseo-web-validator",
        tool="audit_public_webpage",
        target_url="https://demo.example/",
        finding_code=finding_code,
        category="SEO",
        severity=severity,
        message="fixture",
        retrieved_at=NOW - timedelta(minutes=minutes_old),
        result_hash="a" * 64,
    )


def decision(action, classification, evidence_ids, finding_codes=None):
    return DiagnosisDecision(
        classification=classification,
        severity=Severity.HIGH,
        finding_codes=finding_codes or [],
        affected_urls=["https://demo.example/"],
        evidence_ids=evidence_ids,
        proposed_action=action,
        summary="bounded summary",
        reasoning_summary="bounded evidence-based reasoning",
    )


def incident():
    return IncidentRecord(
        incident_id="incident-1",
        fingerprint="f" * 64,
        repository="AKzar1el/searcharis-demo",
        target_origin="https://demo.example",
        affected_url="https://demo.example/",
        finding_code="seo.missing_title",
        state=WorkflowState.VERIFYING,
        github_issue_number=7,
    )


def test_model_cannot_close_when_triggering_finding_is_still_present():
    current = evidence("ev-1", "seo.missing_title")
    result = evaluate_policy(
        decision(
            ProposedAction.CLOSE_INCIDENT,
            DecisionClassification.RECOVERY,
            ["ev-1"],
            ["seo.missing_title"],
        ),
        [current],
        incident(),
        NOW,
    )
    assert result.kind == "DENY"
    assert result.reason_code == "verification_failed"


def test_fresh_recovery_evidence_can_close_open_incident():
    complete = evidence("ev-complete", "validator.audit_complete", severity="info")
    result = evaluate_policy(
        decision(
            ProposedAction.CLOSE_INCIDENT,
            DecisionClassification.RECOVERY,
            ["ev-complete"],
        ),
        [complete],
        incident(),
        NOW,
    )
    assert result.kind == "ALLOW_CLOSE"


def test_regression_can_open_issue_with_fresh_error_evidence():
    finding = evidence("ev-1", "seo.missing_title")
    result = evaluate_policy(
        decision(
            ProposedAction.OPEN_INCIDENT,
            DecisionClassification.REGRESSION,
            ["ev-1"],
            ["seo.missing_title"],
        ),
        [finding],
        None,
        NOW,
    )
    assert result.kind == "ALLOW_OPEN"


def test_unreferenced_evidence_never_mutates():
    finding = evidence("ev-1", "seo.missing_title")
    result = evaluate_policy(
        decision(
            ProposedAction.OPEN_INCIDENT,
            DecisionClassification.REGRESSION,
            ["missing-id"],
            ["seo.missing_title"],
        ),
        [finding],
        None,
        NOW,
    )
    assert result.kind == "DENY"
    assert result.reason_code == "missing_evidence"


def test_stale_evidence_never_mutates():
    stale = evidence("ev-old", "seo.missing_title", minutes_old=11)
    result = evaluate_policy(
        decision(
            ProposedAction.OPEN_INCIDENT,
            DecisionClassification.REGRESSION,
            ["ev-old"],
            ["seo.missing_title"],
        ),
        [stale],
        None,
        NOW,
    )
    assert result.kind == "DENY"
    assert result.reason_code == "stale_evidence"
