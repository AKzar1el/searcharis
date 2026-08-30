from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from searcharis.agent.prompts import DIAGNOSTICIAN_INSTRUCTION
from searcharis.models import DeploymentEvent, DiagnosisDecision, EvidenceRecord, IncidentRecord

MODEL_ID = "gemini-3.7-flash"
TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}
InvokeFn = Callable[[str], Awaitable[str]]
SleepFn = Callable[[float], Awaitable[None]]
JitterFn = Callable[[], float]


class DiagnosticianRetryableError(RuntimeError):
    pass


class DiagnosticianInvalidOutputError(ValueError):
    pass


def _status_code(exc: Exception) -> int | None:
    for attribute in ("status_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
        enum_value = getattr(value, "value", None)
        if isinstance(enum_value, int):
            return enum_value
    return None


def is_retryable_model_error(exc: Exception) -> bool:
    status = _status_code(exc)
    if status is not None:
        return status in TRANSIENT_STATUS_CODES

    message = str(exc).upper()
    transient_markers = (
        "RESOURCE_EXHAUSTED",
        "DEADLINE_EXCEEDED",
        "TEMPORARILY UNAVAILABLE",
        "SERVICE UNAVAILABLE",
        "REQUEST TIMEOUT",
        "TIMED OUT",
        "TIMEOUT",
    )
    if any(marker in message for marker in transient_markers):
        return True

    return any(f"{status} " in message for status in TRANSIENT_STATUS_CODES)


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
    def __init__(
        self,
        invoke: InvokeFn | None = None,
        max_attempts: int = 3,
        sleep_fn: SleepFn = asyncio.sleep,
        jitter_fn: JitterFn = random.random,
    ) -> None:
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        self._invoke = invoke or self._invoke_adk
        self._max_attempts = max_attempts
        self._sleep = sleep_fn
        self._jitter = jitter_fn

    async def diagnose(
        self,
        event: DeploymentEvent,
        evidence: list[EvidenceRecord],
        incident: IncidentRecord | None,
    ) -> DiagnosisDecision:
        prompt = _canonical_prompt(event, evidence, incident)
        last_invalid_output: Exception | None = None
        last_retryable: Exception | None = None

        for attempt in range(self._max_attempts):
            try:
                raw = await self._invoke(prompt)
                return DiagnosisDecision.model_validate_json(raw)
            except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
                last_invalid_output = exc
                last_retryable = None
            except Exception as exc:
                if not is_retryable_model_error(exc):
                    raise
                last_retryable = exc
                last_invalid_output = None
                if attempt < self._max_attempts - 1:
                    base_delay = 0.5 * (2**attempt)
                    jitter = max(0.0, min(float(self._jitter()), 1.0)) * 0.25
                    await self._sleep(min(base_delay + jitter, 2.0))

        if last_retryable is not None:
            raise DiagnosticianRetryableError(
                "diagnostician provider remained unavailable after retry budget"
            ) from last_retryable
        raise DiagnosticianInvalidOutputError(
            "diagnostician did not return a valid DiagnosisDecision"
        ) from last_invalid_output

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
            raise DiagnosticianInvalidOutputError("ADK returned no final structured response")
        return final_text
