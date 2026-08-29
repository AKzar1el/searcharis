from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, HttpUrl

from searcharis.apps.common import build_ingress_dependencies, health_payload
from searcharis.config import Settings
from searcharis.models import DeploymentEvent
from searcharis.ui import render_incident_timeline


@dataclass
class IngressRuntime:
    store: Any
    publisher: Any
    webhook_secret: str
    demo_token: str
    demo_repository: str
    demo_target_url: str


class DemoEventRequest(BaseModel):
    repository: str
    target_url: HttpUrl
    commit_sha: str | None = None


def _runtime_from_settings() -> IngressRuntime:
    settings = Settings()
    store, publisher = build_ingress_dependencies(settings)
    required = {
        "webhook_secret": settings.webhook_secret,
        "demo_token": settings.demo_token,
        "demo_repository": settings.demo_repository,
        "demo_target_url": settings.demo_target_url,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing ingress configuration: {', '.join(missing)}")
    return IngressRuntime(
        store=store,
        publisher=publisher,
        webhook_secret=settings.webhook_secret,
        demo_token=settings.demo_token,
        demo_repository=settings.demo_repository,
        demo_target_url=str(settings.demo_target_url),
    )


def create_ingress_app(runtime: IngressRuntime | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if runtime is not None:
            app.state.runtime = runtime
        else:
            app.state.runtime = _runtime_from_settings()
        yield

    app = FastAPI(title="Searcharis", lifespan=lifespan)
    if runtime is not None:
        app.state.runtime = runtime

    @app.get("/ready")
    async def ready():
        return health_payload("ingress")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        incidents = await request.app.state.runtime.store.list_incidents()
        return HTMLResponse(render_incident_timeline(incidents))

    @app.get("/api/incidents")
    async def incidents(request: Request):
        items = await request.app.state.runtime.store.list_incidents()
        return [item.model_dump(mode="json") for item in items]

    @app.post("/webhooks/github", status_code=202)
    async def github_webhook(
        request: Request,
        x_hub_signature_256: str | None = Header(default=None),
        x_github_event: str | None = Header(default=None),
        x_github_delivery: str | None = Header(default=None),
    ):
        rt: IngressRuntime = request.app.state.runtime
        raw = await request.body()
        expected = "sha256=" + hmac.new(
            rt.webhook_secret.encode("utf-8"), raw, hashlib.sha256
        ).hexdigest()
        if not x_hub_signature_256 or not secrets.compare_digest(x_hub_signature_256, expected):
            raise HTTPException(status_code=401, detail="invalid webhook signature")

        if x_github_event != "deployment_status":
            return {"accepted": False, "reason": "event_not_supported"}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid JSON") from exc

        status = payload.get("deployment_status") or {}
        if status.get("state") != "success":
            return {"accepted": False, "reason": "deployment_not_successful"}
        target_url = status.get("environment_url") or status.get("target_url")
        repository = (payload.get("repository") or {}).get("full_name")
        commit_sha = (payload.get("deployment") or {}).get("sha")
        if not all((target_url, repository, commit_sha, x_github_delivery)):
            raise HTTPException(status_code=400, detail="deployment event is missing required fields")

        event = DeploymentEvent(
            event_id=x_github_delivery,
            repository=repository,
            target_url=target_url,
            commit_sha=commit_sha,
            source="github",
        )
        message_id = await rt.publisher.publish(event)
        return {"accepted": True, "event_id": event.event_id, "message_id": message_id}

    @app.post("/demo/events", status_code=202)
    async def demo_event(
        body: DemoEventRequest,
        request: Request,
        x_searcharis_demo_token: str | None = Header(default=None),
    ):
        rt: IngressRuntime = request.app.state.runtime
        if not x_searcharis_demo_token or not secrets.compare_digest(
            x_searcharis_demo_token, rt.demo_token
        ):
            raise HTTPException(status_code=401, detail="invalid demo token")
        if body.repository != rt.demo_repository or str(body.target_url) != rt.demo_target_url:
            raise HTTPException(status_code=403, detail="demo target is not allowlisted")
        event = DeploymentEvent(
            event_id=uuid4().hex,
            repository=rt.demo_repository,
            target_url=rt.demo_target_url,
            commit_sha=body.commit_sha or f"demo-{uuid4().hex[:12]}",
            source="demo",
        )
        message_id = await rt.publisher.publish(event)
        return {"accepted": True, "event_id": event.event_id, "message_id": message_id}

    return app


app = create_ingress_app()
