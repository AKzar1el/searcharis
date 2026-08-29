from __future__ import annotations

from datetime import datetime, timedelta

from searcharis.models import (
    DecisionClassification,
    DiagnosisDecision,
    EvidenceRecord,
    IncidentRecord,
    PolicyAction,
    PolicyActionKind,
    ProposedAction,
    WorkflowState,
)

_MAX_EVIDENCE_AGE = timedelta(minutes=10)
_PROVIDER_FAILURE_CODES = {"validator.provider_failure"}


def _deny(reason_code: str, reason: str, incident: IncidentRecord | None = None) -> PolicyAction:
    return PolicyAction(
        kind=PolicyActionKind.DENY,
        allowed=False,
        reason_code=reason_code,
        reason=reason,
        incident_id=incident.incident_id if incident else None,
    )


def evaluate_policy(
    decision: DiagnosisDecision,
    evidence: list[EvidenceRecord],
    incident: IncidentRecord | None,
    now: datetime,
) -> PolicyAction:
    by_id = {item.evidence_id: item for item in evidence}
    referenced: list[EvidenceRecord] = []
    for evidence_id in decision.evidence_ids:
        item = by_id.get(evidence_id)
        if item is None:
            return _deny("missing_evidence", "Decision references evidence that is not present.", incident)
        referenced.append(item)

    if not referenced:
        return _deny("missing_evidence", "Mutation decisions require referenced evidence.", incident)

    run_ids = {item.run_id for item in evidence}
    if len(run_ids) > 1:
        return _deny("mixed_runs", "Evidence from multiple runs cannot authorize a mutation.", incident)

    if any(now - item.retrieved_at > _MAX_EVIDENCE_AGE for item in referenced):
        return _deny("stale_evidence", "Referenced evidence is older than ten minutes.", incident)

    if any(item.retrieved_at > now + timedelta(seconds=30) for item in referenced):
        return _deny("future_evidence", "Referenced evidence has an invalid future timestamp.", incident)

    if any(item.finding_code in _PROVIDER_FAILURE_CODES for item in evidence):
        return _deny("provider_failure", "Provider failure cannot authorize a mutation.", incident)

    action = decision.proposed_action

    if action == ProposedAction.OPEN_INCIDENT:
        if decision.classification != DecisionClassification.REGRESSION:
            return _deny("classification_mismatch", "Only a regression can open an incident.", incident)
        if incident is not None and incident.github_issue_number is not None:
            return _deny("incident_exists", "An incident already exists for this fingerprint.", incident)
        if not any(item.severity == "error" for item in referenced):
            return _deny("insufficient_severity", "Opening requires fresh error evidence.", incident)
        return PolicyAction(
            kind=PolicyActionKind.ALLOW_OPEN,
            allowed=True,
            reason_code="fresh_regression",
            reason="Fresh error evidence supports opening a bounded incident.",
            evidence_ids=decision.evidence_ids,
        )

    if action == ProposedAction.CLOSE_INCIDENT:
        if incident is None or incident.github_issue_number is None:
            return _deny("incident_missing", "Closing requires an existing GitHub incident.", incident)
        if incident.state == WorkflowState.RESOLVED:
            return _deny("already_resolved", "Resolved incidents cannot be closed again.", incident)
        if decision.classification != DecisionClassification.RECOVERY:
            return _deny("classification_mismatch", "Only a recovery can close an incident.", incident)
        if any(item.finding_code == incident.finding_code for item in evidence):
            return _deny(
                "verification_failed",
                "The triggering finding is still present in the fresh audit.",
                incident,
            )
        if not any(item.finding_code == "validator.audit_complete" for item in referenced):
            return _deny(
                "verification_incomplete",
                "Closing requires a fresh completed audit evidence record.",
                incident,
            )
        return PolicyAction(
            kind=PolicyActionKind.ALLOW_CLOSE,
            allowed=True,
            reason_code="verified_recovery",
            reason="A fresh completed audit no longer contains the triggering finding.",
            incident_id=incident.incident_id,
            evidence_ids=decision.evidence_ids,
        )

    if action == ProposedAction.COMMENT:
        if incident is None or incident.github_issue_number is None:
            return _deny("incident_missing", "Comments require an existing incident.", incident)
        return PolicyAction(
            kind=PolicyActionKind.ALLOW_COMMENT,
            allowed=True,
            reason_code="fresh_update",
            reason="Fresh referenced evidence supports an incident update.",
            incident_id=incident.incident_id,
            evidence_ids=decision.evidence_ids,
        )

    if action == ProposedAction.RECHECK:
        return PolicyAction(
            kind=PolicyActionKind.RECHECK,
            allowed=False,
            reason_code="recheck_requested",
            reason="The decision requests another read-only verification pass.",
            incident_id=incident.incident_id if incident else None,
            evidence_ids=decision.evidence_ids,
        )

    if action == ProposedAction.ESCALATE or decision.classification == DecisionClassification.NEEDS_REVIEW:
        return PolicyAction(
            kind=PolicyActionKind.ESCALATE,
            allowed=False,
            reason_code="needs_review",
            reason="Ambiguous evidence is routed to human review without mutation.",
            incident_id=incident.incident_id if incident else None,
            evidence_ids=decision.evidence_ids,
        )

    return PolicyAction(
        kind=PolicyActionKind.NONE,
        allowed=False,
        reason_code="no_action",
        reason="No external mutation is authorized.",
        incident_id=incident.incident_id if incident else None,
        evidence_ids=decision.evidence_ids,
    )
