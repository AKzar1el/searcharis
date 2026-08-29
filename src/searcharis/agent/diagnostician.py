from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from searcharis.agent.prompts import DIAGNOSTICIAN_INSTRUCTION
from searcharis.models import DeploymentEvent, DiagnosisDecision, EvidenceRecord, IncidentRecord

MODEL_ID = "gemini-3.7-flash"
InvokeFn = Callable[[str], Awaitable[str]]


def build_root_agent(
    agent_factory: Callable[..., Any] | None = None,
    model_factory: Callable[..., Any] | None = None,
) -> Any:
    if agent_factory is None or model_factory is None:
        try:
            from google.adk.agents import Agent  # type: ignore[import-not-found]
            from google.adk.models import Gemini  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - deployed dependency
            raise RuntimeError("google-adk is required for the production diagnostician") from exc
        agent_factory = agent_factory or Agent
        model_factory = model_factory or Gemini

    return agent_factory(
        name="searcharis_diagnostician",
        model=model_factory(model=MODEL_ID),
        instruction=DIAGNOSTICIAN_INSTRUCTION,
        output_schema=DiagnosisDecision,
    )


def _canonical_prompt(
    event: DeploymentEvent,
    evidence: list[EvidenceRecord],
    incident: IncidentRecord | None,
) -> str:
    payload = {
        "deployment": event.model_dump(mode="json"),
        "incident": incident.model_dump(mode="json") if incident else None,
        "evidence": [item.model_dump(mode="json") for item in evidence],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class Diagnostician:
    def __init__(self, invoke: InvokeFn | None = None, max_attempts: int = 3) -> None:
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        self._invoke = invoke or self._invoke_adk
        self._max_attempts = max_attempts

    async def diagnose(
        self,
        event: DeploymentEvent,
        evidence: list[EvidenceRecord],
        incident: IncidentRecord | None,
    ) -> DiagnosisDecision:
        prompt = _canonical_prompt(event, evidence, incident)
        last_error: Exception | None = None
        for _ in range(self._max_attempts):
            try:
                raw = await self._invoke(prompt)
                return DiagnosisDecision.model_validate_json(raw)
            except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
                last_error = exc
            except Exception as exc:  # transport/runtime errors are bounded too
                last_error = exc

        raise ValueError("diagnostician did not return a valid DiagnosisDecision") from last_error

    async def _invoke_adk(self, prompt: str) -> str:
        try:
            from google.adk.runners import InMemoryRunner  # type: ignore[import-not-found]
            from google.genai import types  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - deployed dependency
            raise RuntimeError("google-adk and google-genai are required for model execution") from exc

        app_name = "searcharis"
        user_id = "searcharis-worker"
        agent = build_root_agent()
        runner = InMemoryRunner(agent=agent, app_name=app_name)
        session = await runner.session_service.create_session(app_name=app_name, user_id=user_id)
        message = types.Content(role="user", parts=[types.Part(text=prompt)])
        final_text: str | None = None

        async for event in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=message,
        ):
            if not getattr(event, "is_final_response", lambda: False)():
                continue
            content = getattr(event, "content", None)
            parts = getattr(content, "parts", None) or []
            text_parts = [getattr(part, "text", None) for part in parts]
            final_text = "".join(part for part in text_parts if isinstance(part, str)).strip()

        if not final_text:
            raise ValueError("ADK returned no final structured response")
        return final_text
