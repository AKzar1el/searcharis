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


@pytest.mark.asyncio
async def test_find_issue_by_marker_scans_bounded_recent_issue_page():
    marker = "<!-- searcharis-action:abc -->"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "number": 42,
                    "html_url": "https://github.com/AKzar1el/searcharis/issues/42",
                    "body": f"Regression\n{marker}",
                }
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    broker = GitHubIssueBroker("token", client=client)

    finder = getattr(broker, "find_issue_by_marker", None)
    assert callable(finder), "GitHub issue reconciliation read is missing"
    issue = await finder("AKzar1el/searcharis", marker)

    assert issue is not None
    assert issue.number == 42
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.params["state"] == "all"
    assert requests[0].url.params["per_page"] == "100"
    await client.aclose()


@pytest.mark.asyncio
async def test_comment_exists_detects_marker_without_posting_duplicate():
    marker = "<!-- searcharis-action:verify -->"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[{"body": f"Verified.\n{marker}"}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    broker = GitHubIssueBroker("token", client=client)

    checker = getattr(broker, "comment_exists", None)
    assert callable(checker), "GitHub comment reconciliation read is missing"
    exists = await checker("AKzar1el/searcharis", 42, marker)

    assert exists is True
    assert [request.method for request in requests] == ["GET"]
    await client.aclose()


@pytest.mark.asyncio
async def test_get_issue_state_supports_idempotent_close_reconciliation():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"state": "closed"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    broker = GitHubIssueBroker("token", client=client)

    getter = getattr(broker, "get_issue_state", None)
    assert callable(getter), "GitHub issue-state reconciliation read is missing"
    state = await getter("AKzar1el/searcharis", 42)

    assert state == "closed"
    await client.aclose()
