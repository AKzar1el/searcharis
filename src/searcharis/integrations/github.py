from __future__ import annotations

import re

import httpx
from pydantic import BaseModel, HttpUrl

_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubIssue(BaseModel):
    number: int
    html_url: HttpUrl


class GitHubIssueBroker:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None) -> None:
        if not token:
            raise ValueError("GitHub token is required")
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "Searcharis/0.1",
        }

    @staticmethod
    def _repo_path(repository: str) -> str:
        if not _REPOSITORY_RE.fullmatch(repository):
            raise ValueError("repository must be in owner/name form")
        return repository

    async def open_incident(self, repository: str, title: str, body: str) -> GitHubIssue:
        repo = self._repo_path(repository)
        response = await self._client.post(
            f"https://api.github.com/repos/{repo}/issues",
            headers=self._headers,
            json={"title": title, "body": body},
        )
        response.raise_for_status()
        return GitHubIssue.model_validate(response.json())

    async def comment_incident(self, repository: str, issue_number: int, body: str) -> None:
        repo = self._repo_path(repository)
        response = await self._client.post(
            f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments",
            headers=self._headers,
            json={"body": body},
        )
        response.raise_for_status()

    async def close_incident(self, repository: str, issue_number: int) -> None:
        repo = self._repo_path(repository)
        response = await self._client.patch(
            f"https://api.github.com/repos/{repo}/issues/{issue_number}",
            headers=self._headers,
            json={"state": "closed", "state_reason": "completed"},
        )
        response.raise_for_status()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
