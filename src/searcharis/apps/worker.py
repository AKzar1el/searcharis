from __future__ import annotations

import base64
import binascii
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError

from searcharis.apps.common import build_worker_orchestrator, health_payload
from searcharis.config import Settings
from searcharis.models import DeploymentEvent, WorkflowState


class VerificationRequest(BaseModel):
    event_id: str
    incident_id: str


def create_worker_app(orchestrator: Any | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.orchestrator = orchestrator or build_worker_orchestrator(Settings())
        yield

    app = FastAPI(title="Searcharis Worker", lifespan=lifespan)
    if orchestrator is not None:
        app.state.orchestrator = orchestrator

    @app.get("/healthz")
    async def healthz():
        return health_payload("worker")

    @app.post("/internal/pubsub")
    async def pubsub(request: Request):
        try:
            envelope = await request.json()
            encoded = envelope["message"]["data"]
            raw = base64.b64decode(encoded, validate=True)
            event = DeploymentEvent.model_validate_json(raw)
        except (KeyError, TypeError, ValueError, binascii.Error, ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="invalid Pub/Sub envelope") from exc

        run = await request.app.state.orchestrator.process_deployment(event)
        if run.state == WorkflowState.FAILED_RETRYABLE:
            raise HTTPException(status_code=500, detail="retryable workflow failure")
        return Response(status_code=204)

    @app.post("/internal/verify")
    async def verify(body: VerificationRequest, request: Request):
        run = await request.app.state.orchestrator.verify(body.event_id, body.incident_id)
        if run.state == WorkflowState.FAILED_RETRYABLE:
            raise HTTPException(status_code=500, detail="retryable verification failure")
        return Response(status_code=204)

    return app


app = create_worker_app()
