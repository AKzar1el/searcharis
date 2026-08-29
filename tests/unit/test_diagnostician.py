import json

import pytest

from searcharis.agent.diagnostician import MODEL_ID, Diagnostician, build_root_agent
from searcharis.models import (
    DecisionClassification,
    DeploymentEvent,
    DiagnosisDecision,
    EvidenceRecord,
    ProposedAction,
    Severity,
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

    event = DeploymentEvent(
        event_id="evt-1",
        repository="AKzar1el/searcharis-demo",
        target_url="https://demo.example/",
        commit_sha="1234567",
        source="demo",
    )
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
async def test_diagnostician_fails_closed_after_retry_budget():
    async def invoke(prompt: str) -> str:
        return "still-not-json"

    event = DeploymentEvent(
        event_id="evt-1",
        repository="AKzar1el/searcharis-demo",
        target_url="https://demo.example/",
        commit_sha="1234567",
        source="demo",
    )
    diagnostician = Diagnostician(invoke=invoke, max_attempts=3)

    with pytest.raises(ValueError, match="valid DiagnosisDecision"):
        await diagnostician.diagnose(event, [], None)
