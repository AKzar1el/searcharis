import json

import httpx
import pytest

from searcharis.integrations.github import GitHubIssueBroker


@pytest.mark.asyncio
async def test_broker_uses_only_narrow_issue_routes_and_exact_close_payload():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, str(request.url), body, request.headers))
        if request.method == "POST" and request.url.path.endswith("/issues"):
            return httpx.Response(
                201,
                json={"number": 42, "html_url": "https://github.com/AKzar1el/demo/issues/42"},
            )
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    broker = GitHubIssueBroker("token", client=client)

    issue = await broker.open_incident("AKzar1el/demo", "Regression", "Evidence body")
    await broker.comment_incident("AKzar1el/demo", issue.number, "Verification update")
    await broker.close_incident("AKzar1el/demo", issue.number)
    await client.aclose()

    assert [(method, url) for method, url, _, _ in seen] == [
        ("POST", "https://api.github.com/repos/AKzar1el/demo/issues"),
        ("POST", "https://api.github.com/repos/AKzar1el/demo/issues/42/comments"),
        ("PATCH", "https://api.github.com/repos/AKzar1el/demo/issues/42"),
    ]
    assert seen[2][2] == {"state": "closed", "state_reason": "completed"}
    assert seen[0][3]["authorization"] == "Bearer token"
    assert seen[0][3]["x-github-api-version"] == "2026-03-10"
