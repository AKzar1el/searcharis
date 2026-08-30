import pytest

from searcharis.agent import diagnostician as diagnostician_module
from searcharis.models import DeploymentEvent, EvidenceRecord, WorkflowState
from searcharis.services.orchestrator import Orchestrator
from searcharis.storage.memory import InMemoryStateStore


class FakeValidator:
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
            )
        ]


class RaisingDiagnostician:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def diagnose(self, event, evidence, incident):
        raise self._exc


class UnusedGitHub:
    pass


class UnusedScheduler:
    pass


def _event() -> DeploymentEvent:
    return DeploymentEvent(
        event_id="evt-model-failure",
        repository="AKzar1el/searcharis",
        target_url="https://demo.example/",
        commit_sha="1234567",
        source="demo",
    )


@pytest.mark.asyncio
async def test_retryable_diagnostician_error_maps_to_failed_retryable():
    retryable_error = getattr(diagnostician_module, "DiagnosticianRetryableError", None)
    assert retryable_error is not None, "retryable diagnostician error type is missing"

    orchestrator = Orchestrator(
        store=InMemoryStateStore(),
        validator=FakeValidator(),
        diagnostician=RaisingDiagnostician(retryable_error("provider exhausted")),
        github=UnusedGitHub(),
        scheduler=UnusedScheduler(),
    )

    run = await orchestrator.process_deployment(_event())

    assert run.state == WorkflowState.FAILED_RETRYABLE
    assert "provider exhausted" in (run.error or "")


@pytest.mark.asyncio
async def test_invalid_diagnostician_output_maps_to_needs_review():
    invalid_output_error = getattr(diagnostician_module, "DiagnosticianInvalidOutputError", None)
    assert invalid_output_error is not None, "invalid-output diagnostician error type is missing"

    orchestrator = Orchestrator(
        store=InMemoryStateStore(),
        validator=FakeValidator(),
        diagnostician=RaisingDiagnostician(invalid_output_error("invalid structured output")),
        github=UnusedGitHub(),
        scheduler=UnusedScheduler(),
    )

    run = await orchestrator.process_deployment(_event())

    assert run.state == WorkflowState.NEEDS_REVIEW
    assert "invalid structured output" in (run.error or "")
