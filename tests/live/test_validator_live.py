import os

import pytest

from searcharis.integrations.validator import ValidatorClient

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_hosted_validator_returns_structured_evidence():
    url = os.getenv("SEARCHARIS_LIVE_TEST_URL")
    if not url:
        pytest.skip("SEARCHARIS_LIVE_TEST_URL is not configured")
    client = ValidatorClient(
        os.getenv(
            "SEARCHARIS_VALIDATOR_MCP_URL",
            "https://web-validator-mcp.digestseo.com/mcp",
        )
    )
    evidence = await client.audit_page(url, "live-test")
    assert evidence
    assert evidence[0].finding_code == "validator.audit_complete"
