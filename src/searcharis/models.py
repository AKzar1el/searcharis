from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class WorkflowState(StrEnum):
    RECEIVED = "RECEIVED"
    AUDITING = "AUDITING"
    DECIDED = "DECIDED"
    ACTIONED = "ACTIONED"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"


class DecisionClassification(StrEnum):
    REGRESSION = "REGRESSION"
    RECOVERY = "RECOVERY"
    NO_ACTION = "NO_ACTION"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ProposedAction(StrEnum):
    OPEN_INCIDENT = "OPEN_INCIDENT"
    COMMENT = "COMMENT"
    CLOSE_INCIDENT = "CLOSE_INCIDENT"
    RECHECK = "RECHECK"
    ESCALATE = "ESCALATE"
    NONE = "NONE"


class PolicyActionKind(StrEnum):
    ALLOW_OPEN = "ALLOW_OPEN"
    ALLOW_COMMENT = "ALLOW_COMMENT"
    ALLOW_CLOSE = "ALLOW_CLOSE"
    RECHECK = "RECHECK"
    ESCALATE = "ESCALATE"
    NONE = "NONE"
    DENY = "DENY"


class DeploymentEvent(BaseModel):
    event_id: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    target_url: HttpUrl
    commit_sha: str = Field(min_length=7)
    ref: str = "refs/heads/main"
    deployed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: Literal["github", "demo"] = "github"


class EvidenceRecord(BaseModel):
    evidence_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    target_url: HttpUrl
    finding_code: str = Field(min_length=1)
    category: str = Field(min_length=1)
    severity: Literal["error", "warning", "info"]
    message: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    result_hash: str = Field(min_length=1)


class DiagnosisDecision(BaseModel):
    classification: DecisionClassification
    severity: Severity
    finding_codes: list[str]
    affected_urls: list[HttpUrl]
    evidence_ids: list[str]
    proposed_action: ProposedAction
    summary: str = Field(min_length=1, max_length=1000)
    reasoning_summary: str = Field(min_length=1, max_length=2000)


class IncidentRecord(BaseModel):
    incident_id: str = Field(min_length=1)
    fingerprint: str = Field(min_length=64, max_length=64)
    repository: str = Field(min_length=1)
    target_origin: str = Field(min_length=1)
    affected_url: HttpUrl
    finding_code: str = Field(min_length=1)
    state: WorkflowState = WorkflowState.RECEIVED
    triggering_evidence_id: str | None = None
    github_issue_number: int | None = None
    github_issue_url: HttpUrl | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PolicyAction(BaseModel):
    kind: PolicyActionKind
    allowed: bool
    reason_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    incident_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ActionClaim(BaseModel):
    acquired: bool
    stale_takeover: bool = False
    completed: bool = False
    result: dict[str, Any] | None = None


class RunRecord(BaseModel):
    run_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    state: WorkflowState
    evidence_ids: list[str] = Field(default_factory=list)
    decision: DiagnosisDecision | None = None
    policy_action: PolicyAction | None = None
    incident_id: str | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
