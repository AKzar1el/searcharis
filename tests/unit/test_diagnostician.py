import json

import pytest

from searcharis.agent import diagnostician as diagnostician_module
from searcharis.agent.diagnostician import MODEL_ID, Diagnostician, build_root_agent
from searcharis.models import (
    DecisionClassification,
    DeploymentEvent,
    DiagnosisDecision,
    EvidenceRecord,
    ProposedAction,
    Severity,
)


class FakeProviderError(RuntimeError):
    def __init__(self, status_code: int, message: str = "provider failure") -> None:
        super().__init__(message)
        self.status_code = status_code


def _event() -> DeploymentEvent:
    return DeploymentEvent(
        event_id="evt-1",
        repository="AKzar1el/searcharis-demo",
        target_url="https://demo.example/",
        commit_sha="1234567",
        source="demo",
    )


def test_agent_contract_uses_current_gemini_wrapper_schema_and_no_tools():
    captured = {}
    model_args = {}

    def fake_agent_factory(**kwargs):
        captured.update(kwargs)
        return object()

    def fake_model_factory(**kwargs):
        model_args.update(kwargs)
        return {"wrapped_model": kwargs["model"]}

    build_root_agent(
        agent_factory=fake_agent_factory,
        model_factory=fake_model_factory,
    )

    assert MODEL_ID == "gemini-3.7-flash"
    assert model_args["model"] == "gemini-3.7-flash"
    assert captured["model"] == {"wrapped_model": "gemini-3.7-flash"}
    assert captured["output_schema"] is DiagnosisDecision
    assert "tools" not in captured or captured["tools"] == []


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_model_statuses_are_retryable(status):
    classifier = getattr(diagnostician_module, "is_retryable_model_error", None)
    assert callable(classifier), "retryable model error classifier is missing"
    assert classifier(FakeProviderError(status)) is True


def test_non_transient_status_and_invalid_output_are_not_provider_retryable():
    classifier = getattr(diagnostician_module, "is_retryable_model_error", None)
    assert callable(classifier), "retryable model error classifier is missing"
    assert classifier(FakeProviderError(400)) is False
    assert classifier(ValueError("invalid model output")) is False


@pytest.mark.parametrize(
    "message",
    [
        "429 RESOURCE_EXHAUSTED",
        "DEADLINE_EXCEEDED while calling model",
        "503 temporarily unavailable",
    ],
)
def test_known_transient_provider_messages_are_retryable(message):
    classifier = getattr(diagnostician_module, "is_retryable_model_error", None)
    assert callable(classifier), "retryable model error classifier is missing"
    assert classifier(RuntimeError(message)) is True


@pytest.mark.asyncio
async def test_diagnostician_retries_transient_failures_with_bounded_backoff():
    retryable_error = getattr(diagnostician_module, "DiagnosticianRetryableError", None)
    assert retryable_error is not None, "retryable diagnostician error type is missing"

    calls = 0
    sleeps: list[float] = []

    async def invoke(prompt: str) -> str:
        nonlocal calls
        calls += 1
        raise FakeProviderError(429, "RESOURCE_EXHAUSTED")

    async def sleep_fn(delay: float) -> None:
        sleeps.append(delay)

    diagnostician = Diagnostician(
        invoke=invoke,
        max_attempts=3,
        sleep_fn=sleep_fn,
        jitter_fn=lambda: 0.0,
    )

    with pytest.raises(retryable_error):
        await diagnostician.diagnose(_event(), [], None)

    assert calls == 3
    assert sleeps == [0.5, 1.0]


@pytest.mark.asyncio
async def test_diagnostician_retries_malformed_output_then_returns_valid_decision():
    calls = 0

    async def invoke(prompt: str) -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            return "not-json"
        return json.dumps(
            {
                "classification": "REGRESSION",
                "severity": "HIGH",
                "finding_codes": ["seo.missing_title"],
                "affected_urls": ["https://demo.example/"],
                "evidence_ids": ["ev-1"],
                "proposed_action": "OPEN_INCIDENT",
                "summary": "Title missing after deployment.",
                "reasoning_summary": "Fresh validator evidence reports the missing title.",
            }
        )

    event = _event()
    evidence = [
        EvidenceRecord(
            evidence_id="ev-1",
            run_id="run-1",
            provider="digestseo-web-validator",
            tool="audit_public_webpage",
            target_url=event.target_url,
            finding_code="seo.missing_title",
            category="SEO",
            severity="error",
            message="Missing or empty <title> tag.",
            result_hash="a" * 64,
        )
    ]
    diagnostician = Diagnostician(invoke=invoke, max_attempts=3)

    result = await diagnostician.diagnose(event, evidence, None)

    assert calls == 3
    assert result.classification == DecisionClassification.REGRESSION
    assert result.severity == Severity.HIGH
    assert result.proposed_action == ProposedAction.OPEN_INCIDENT


@pytest.mark.asyncio
async def test_diagnostician_invalid_output_raises_explicit_invalid_output_error():
    invalid_output_error = getattr(diagnostician_module, "DiagnosticianInvalidOutputError", None)
    assert invalid_output_error is not None, "invalid-output diagnostician error type is missing"

    async def invoke(prompt: str) -> str:
        return "still-not-json"

    diagnostician = Diagnostician(invoke=invoke, max_attempts=3)

    with pytest.raises(invalid_output_error, match="valid DiagnosisDecision"):
        await diagnostician.diagnose(_event(), [], None)
