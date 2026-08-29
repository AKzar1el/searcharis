from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import RootModel

from searcharis.integrations.validator import (
    ValidatorClient,
    ValidatorProviderError,
    normalize_validator_result,
)

NOW = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)


def test_known_validator_messages_map_to_stable_application_codes():
    payload = {
        "seo_findings": [
            {"severity": "error", "category": "SEO", "message": "Missing or empty <title> tag."},
            {"severity": "error", "category": "SEO", "message": "Missing or empty meta description."},
            {"severity": "warning", "category": "SEO", "message": "Missing canonical link tag."},
            {"severity": "error", "category": "SEO", "message": "Missing viewport meta tag."},
            {"severity": "error", "category": "SEO", "message": "Missing an H1 heading."},
        ],
        "schema_findings": [],
        "html_messages": [],
        "failed_checks": [],
    }

    evidence = normalize_validator_result(payload, "https://demo.example/", "run-1", NOW)
    assert [item.finding_code for item in evidence] == [
        "validator.audit_complete",
        "seo.missing_title",
        "seo.missing_description",
        "seo.missing_canonical",
        "seo.missing_viewport",
        "seo.missing_h1",
    ]


def test_unknown_finding_code_is_stable_and_namespaced():
    payload = {
        "seo_findings": [
            {"severity": "warning", "category": "SEO", "message": "A future validator finding."}
        ],
        "schema_findings": [],
        "html_messages": [],
        "failed_checks": [],
    }
    first = normalize_validator_result(payload, "https://demo.example/", "run-1", NOW)
    second = normalize_validator_result(payload, "https://demo.example/", "run-1", NOW)
    assert first[1].finding_code == second[1].finding_code
    assert first[1].finding_code.startswith("validator.")


def test_fetch_failure_is_not_treated_as_clean_audit():
    with pytest.raises(ValidatorProviderError):
        normalize_validator_result(
            {
                "seo_findings": [],
                "schema_findings": [],
                "html_messages": [],
                "failed_checks": ["fetch"],
            },
            "https://demo.example/",
            "run-1",
            NOW,
        )


@pytest.mark.asyncio
async def test_validator_client_accepts_fastmcp_typed_data_with_structured_content(monkeypatch):
    payload = {
        "seo_findings": [],
        "schema_findings": [],
        "html_messages": [],
        "failed_checks": [],
    }
    typed_data = RootModel[dict[str, object]](payload)

    class FakeClient:
        def __init__(self, mcp_url: str):
            self.mcp_url = mcp_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def call_tool(self, name: str, arguments: dict[str, object]):
            assert name == "audit_public_webpage"
            assert arguments == {"url": "https://demo.example/", "check_links": False}
            return SimpleNamespace(data=typed_data, structured_content=payload, is_error=False)

    import fastmcp

    monkeypatch.setattr(fastmcp, "Client", FakeClient)

    evidence = await ValidatorClient("https://validator.example/mcp").audit_page(
        "https://demo.example/", "run-1"
    )

    assert evidence[0].finding_code == "validator.audit_complete"
