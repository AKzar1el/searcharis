from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from searcharis.agent.diagnostician import (
    DiagnosticianInvalidOutputError,
    DiagnosticianRetryableError,
)
from searcharis.ids import action_key, incident_fingerprint
from searcharis.models import (
    DeploymentEvent,
    EvidenceRecord,
    IncidentRecord,
    PolicyActionKind,
    RunRecord,
    WorkflowState,
)
from searcharis.policy import evaluate_policy
from searcharis.state_machine import assert_transition


class Orchestrator:
    def __init__(
        self,
        *,
        store: Any,
        validator: Any,
        diagnostician: Any,
        github: Any,
        scheduler: Any,
    ) -> None:
        self._store = store
        self._validator = validator
        self._diagnostician = diagnostician
        self._github = github
        self._scheduler = scheduler

    async def process_deployment(self, event: DeploymentEvent) -> RunRecord:
        await self._store.put_event(event)
        existing = await self._find_open_incident(event)
        return await self._execute(event, existing)

    async def verify(self, event_id: str, incident_id: str) -> RunRecord:
        event = await self._store.get_event(event_id)
        if event is None:
            raise KeyError(f"unknown event: {event_id}")
        incident = await self._store.get_incident(incident_id)
        if incident is None:
            raise KeyError(f"unknown incident: {incident_id}")
        return await self._execute(event, incident)

    async def _execute(
        self,
        event: DeploymentEvent,
        incident: IncidentRecord | None,
    ) -> RunRecord:
        run = RunRecord(
            run_id=uuid4().hex,
            event_id=event.event_id,
            state=WorkflowState.RECEIVED,
            incident_id=incident.incident_id if incident else None,
        )
        await self._store.create_run(run)
        run = await self._move_run(run, WorkflowState.AUDITING)

        try:
            evidence = await self._validator.audit_page(str(event.target_url), run.run_id)
        except Exception as exc:
            run = await self._move_run(
                run,
                WorkflowState.FAILED_RETRYABLE,
                error=f"validator: {type(exc).__name__}: {exc}",
            )
            return run

        for item in evidence:
            if item.run_id != run.run_id:
                run = await self._move_run(
                    run,
                    WorkflowState.FAILED_TERMINAL,
                    error="validator returned evidence for a different run",
                )
                return run
            await self._store.save_evidence(item)
        run = run.model_copy(
            update={
                "evidence_ids": [item.evidence_id for item in evidence],
                "updated_at": datetime.now(UTC),
            }
        )
        await self._store.update_run(run)

        try:
            decision = await self._diagnostician.diagnose(event, evidence, incident)
        except DiagnosticianRetryableError as exc:
            run = await self._move_run(
                run,
                WorkflowState.FAILED_RETRYABLE,
                error=f"diagnostician: {type(exc).__name__}: {exc}",
            )
            return run
        except DiagnosticianInvalidOutputError as exc:
            run = await self._move_run(
                run,
                WorkflowState.NEEDS_REVIEW,
                error=f"diagnostician: {type(exc).__name__}: {exc}",
            )
            return run
        except Exception as exc:
            run = await self._move_run(
                run,
                WorkflowState.NEEDS_REVIEW,
                error=f"diagnostician: {type(exc).__name__}: {exc}",
            )
            return run

        run = run.model_copy(update={"decision": decision, "updated_at": datetime.now(UTC)})
        await self._store.update_run(run)
        run = await self._move_run(run, WorkflowState.DECIDED)

        policy_action = evaluate_policy(decision, evidence, incident, datetime.now(UTC))
        run = run.model_copy(update={"policy_action": policy_action, "updated_at": datetime.now(UTC)})
        await self._store.update_run(run)

        if (
            incident is not None
            and incident.state == WorkflowState.ACTIONED
            and incident.github_issue_number is not None
        ):
            try:
                await self._scheduler.schedule(event.event_id, incident.incident_id, 30)
            except Exception as exc:
                return await self._move_run(
                    run,
                    WorkflowState.FAILED_RETRYABLE,
                    error=f"scheduler: {type(exc).__name__}: {exc}",
                )
            incident = incident.model_copy(
                update={"state": WorkflowState.VERIFYING, "updated_at": datetime.now(UTC)}
            )
            await self._store.upsert_incident(incident)
            run = await self._move_run(run, WorkflowState.ACTIONED, incident_id=incident.incident_id)
            return await self._move_run(run, WorkflowState.VERIFYING)

        if policy_action.kind == PolicyActionKind.ALLOW_OPEN:
            return await self._open_incident(run, event, evidence, decision)
        if policy_action.kind == PolicyActionKind.ALLOW_CLOSE and incident is not None:
            return await self._close_incident(run, event, evidence, incident)
        if policy_action.kind == PolicyActionKind.ALLOW_COMMENT and incident is not None:
            return await self._comment_incident(run, event, evidence, incident)

        if incident is not None and incident.state == WorkflowState.VERIFYING:
            return await self._move_run(run, WorkflowState.VERIFYING)
        if policy_action.kind == PolicyActionKind.ESCALATE:
            return await self._move_run(run, WorkflowState.NEEDS_REVIEW)
        return run

    async def _open_incident(
        self,
        run: RunRecord,
        event: DeploymentEvent,
        evidence: list[EvidenceRecord],
        decision: Any,
    ) -> RunRecord:
        finding = self._primary_finding(evidence, decision.finding_codes)
        if finding is None:
            return await self._move_run(
                run,
                WorkflowState.NEEDS_REVIEW,
                error="policy allowed open without a concrete error finding",
            )

        target_url = str(event.target_url)
        split = urlsplit(target_url)
        target_origin = f"{split.scheme}://{split.netloc}"
        incident_id = incident_fingerprint(
            event.repository,
            target_origin,
            target_url,
            finding.finding_code,
        )
        key = action_key("open", incident_id, finding.result_hash)
        claimed = await self._store.claim_action(key)
        if not claimed:
            existing = await self._store.get_incident(incident_id)
            if existing and existing.state == WorkflowState.ACTIONED:
                await self._scheduler.schedule(event.event_id, incident_id, 30)
                existing = existing.model_copy(
                    update={"state": WorkflowState.VERIFYING, "updated_at": datetime.now(UTC)}
                )
                await self._store.upsert_incident(existing)
                return await self._move_run(run, WorkflowState.VERIFYING, incident_id=incident_id)
            return run.model_copy(update={"incident_id": incident_id})

        try:
            issue = await self._github.open_incident(
                event.repository,
                f"[Searcharis] Search regression: {finding.finding_code}",
                self._issue_body(event, finding, decision.summary),
            )
        except Exception as exc:
            await self._store.release_action(key)
            return await self._move_run(
                run,
                WorkflowState.FAILED_RETRYABLE,
                error=f"github open: {type(exc).__name__}: {exc}",
            )
        await self._store.complete_action(
            key,
            {"issue_number": issue.number, "issue_url": str(issue.html_url)},
        )

        incident = IncidentRecord(
            incident_id=incident_id,
            fingerprint=incident_id,
            repository=event.repository,
            target_origin=target_origin,
            affected_url=event.target_url,
            finding_code=finding.finding_code,
            state=WorkflowState.ACTIONED,
            triggering_evidence_id=finding.evidence_id,
            github_issue_number=issue.number,
            github_issue_url=issue.html_url,
        )
        await self._store.upsert_incident(incident)
        run = await self._move_run(run, WorkflowState.ACTIONED, incident_id=incident_id)

        try:
            await self._scheduler.schedule(event.event_id, incident_id, 30)
        except Exception as exc:
            return await self._move_run(
                run,
                WorkflowState.FAILED_RETRYABLE,
                error=f"scheduler: {type(exc).__name__}: {exc}",
            )
        incident = incident.model_copy(
            update={"state": WorkflowState.VERIFYING, "updated_at": datetime.now(UTC)}
        )
        await self._store.upsert_incident(incident)
        return await self._move_run(run, WorkflowState.VERIFYING)

    async def _close_incident(
        self,
        run: RunRecord,
        event: DeploymentEvent,
        evidence: list[EvidenceRecord],
        incident: IncidentRecord,
    ) -> RunRecord:
        run = await self._move_run(run, WorkflowState.VERIFYING)
        evidence_hash = self._evidence_hash(evidence)

        comment_key = action_key("verification-comment", incident.incident_id, evidence_hash)
        if await self._store.claim_action(comment_key):
            try:
                await self._github.comment_incident(
                    event.repository,
                    incident.github_issue_number,
                    self._verification_comment(incident, evidence),
                )
            except Exception as exc:
                await self._store.release_action(comment_key)
                return await self._move_run(
                    run,
                    WorkflowState.FAILED_RETRYABLE,
                    error=f"github comment: {type(exc).__name__}: {exc}",
                )
            await self._store.complete_action(comment_key, {"commented": True})

        close_key = action_key("close", incident.incident_id, evidence_hash)
        closed_now = False
        if await self._store.claim_action(close_key):
            try:
                await self._github.close_incident(event.repository, incident.github_issue_number)
            except Exception as exc:
                await self._store.release_action(close_key)
                return await self._move_run(
                    run,
                    WorkflowState.FAILED_RETRYABLE,
                    error=f"github close: {type(exc).__name__}: {exc}",
                )
            await self._store.complete_action(close_key, {"closed": True})
            closed_now = True

        if closed_now:
            incident = incident.model_copy(
                update={"state": WorkflowState.RESOLVED, "updated_at": datetime.now(UTC)}
            )
            await self._store.upsert_incident(incident)
            return await self._move_run(run, WorkflowState.RESOLVED)
        return run

    async def _comment_incident(
        self,
        run: RunRecord,
        event: DeploymentEvent,
        evidence: list[EvidenceRecord],
        incident: IncidentRecord,
    ) -> RunRecord:
        evidence_hash = self._evidence_hash(evidence)
        key = action_key("comment", incident.incident_id, evidence_hash)
        if await self._store.claim_action(key):
            try:
                await self._github.comment_incident(
                    event.repository,
                    incident.github_issue_number,
                    "Searcharis observed new fresh evidence for this incident.",
                )
            except Exception as exc:
                await self._store.release_action(key)
                return await self._move_run(
                    run,
                    WorkflowState.FAILED_RETRYABLE,
                    error=f"github comment: {type(exc).__name__}: {exc}",
                )
            await self._store.complete_action(key, {"commented": True})
        return await self._move_run(run, WorkflowState.ACTIONED)

    async def _find_open_incident(self, event: DeploymentEvent) -> IncidentRecord | None:
        return await self._store.find_active_incident(event.repository, str(event.target_url))

    async def _move_run(
        self,
        run: RunRecord,
        target: WorkflowState,
        *,
        error: str | None = None,
        incident_id: str | None = None,
    ) -> RunRecord:
        assert_transition(run.state, target)
        run = run.model_copy(
            update={
                "state": target,
                "error": error if error is not None else run.error,
                "incident_id": incident_id if incident_id is not None else run.incident_id,
                "updated_at": datetime.now(UTC),
            }
        )
        await self._store.update_run(run)
        return run

    @staticmethod
    def _primary_finding(
        evidence: list[EvidenceRecord],
        decision_codes: list[str],
    ) -> EvidenceRecord | None:
        errors = [item for item in evidence if item.severity == "error"]
        for code in decision_codes:
            for item in errors:
                if item.finding_code == code:
                    return item
        return errors[0] if errors else None

    @staticmethod
    def _evidence_hash(evidence: list[EvidenceRecord]) -> str:
        complete = next(
            (item.result_hash for item in evidence if item.finding_code == "validator.audit_complete"),
            None,
        )
        if complete is None:
            raise ValueError("completed audit evidence is required")
        return complete

    @staticmethod
    def _issue_body(event: DeploymentEvent, finding: EvidenceRecord, summary: str) -> str:
        return (
            "## Searcharis detected a post-deployment search regression\n\n"
            f"- Deployment: `{event.commit_sha}`\n"
            f"- URL: {event.target_url}\n"
            f"- Finding: `{finding.finding_code}`\n"
            f"- Evidence: `{finding.evidence_id}`\n\n"
            f"{summary}\n\n"
            "This issue is managed by Searcharis. It will only be closed after a fresh external "
            "audit proves the triggering finding is absent."
        )

    @staticmethod
    def _verification_comment(
        incident: IncidentRecord,
        evidence: list[EvidenceRecord],
    ) -> str:
        complete = next(item for item in evidence if item.finding_code == "validator.audit_complete")
        return (
            "Searcharis verification passed. A fresh external audit completed and no longer "
            f"contains `{incident.finding_code}`. Evidence result hash: `{complete.result_hash}`."
        )
