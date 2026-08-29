from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from searcharis.models import EvidenceRecord


class ValidatorProviderError(RuntimeError):
    pass


_KNOWN_MESSAGES = {
    "Missing or empty <title> tag.": "seo.missing_title",
    "Missing or empty meta description.": "seo.missing_description",
    "Missing canonical link tag.": "seo.missing_canonical",
    "Missing viewport meta tag.": "seo.missing_viewport",
    "Missing an H1 heading.": "seo.missing_h1",
}


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _finding_code(category: str, message: str) -> str:
    known = _KNOWN_MESSAGES.get(message)
    if known:
        return known
    digest = hashlib.sha256(f"{category}\0{message}".encode()).hexdigest()[:20]
    return f"validator.{digest}"


def _evidence_id(run_id: str, target_url: str, finding_code: str, message: str, result_hash: str) -> str:
    return _canonical_digest(
        {
            "run_id": run_id,
            "target_url": target_url,
            "finding_code": finding_code,
            "message": message,
            "result_hash": result_hash,
        }
    )


def normalize_validator_result(
    data: dict[str, Any],
    target_url: str,
    run_id: str,
    retrieved_at: datetime | None = None,
) -> list[EvidenceRecord]:
    failed_checks = data.get("failed_checks")
    if not isinstance(failed_checks, list):
        raise ValidatorProviderError("Validator response is missing failed_checks.")
    if "fetch" in failed_checks:
        raise ValidatorProviderError("Validator could not fetch the target page.")

    result_hash = _canonical_digest(data)
    timestamp = retrieved_at or datetime.now(UTC)
    records: list[EvidenceRecord] = []

    complete_code = "validator.audit_complete"
    complete_message = "Public webpage audit completed with structured evidence."
    records.append(
        EvidenceRecord(
            evidence_id=_evidence_id(
                run_id, target_url, complete_code, complete_message, result_hash
            ),
            run_id=run_id,
            provider="digestseo-web-validator",
            tool="audit_public_webpage",
            target_url=target_url,
            finding_code=complete_code,
            category="Audit",
            severity="info",
            message=complete_message,
            details={"failed_checks": list(failed_checks)},
            retrieved_at=timestamp,
            result_hash=result_hash,
        )
    )

    raw_findings: list[dict[str, Any]] = []
    for key in ("seo_findings", "schema_findings"):
        value = data.get(key, [])
        if not isinstance(value, list):
            raise ValidatorProviderError(f"Validator response field {key} is not a list.")
        raw_findings.extend(item for item in value if isinstance(item, dict))

    html_messages = data.get("html_messages", [])
    if not isinstance(html_messages, list):
        raise ValidatorProviderError("Validator response field html_messages is not a list.")
    for message in html_messages:
        if not isinstance(message, dict):
            continue
        raw_findings.append(
            {
                "severity": message.get("type", "info"),
                "category": "HTML",
                "message": message.get("message", ""),
                "line": message.get("line"),
                "column": message.get("column"),
            }
        )

    for item in raw_findings:
        message = str(item.get("message", "")).strip()
        if not message:
            continue
        category = str(item.get("category", "Validator")).strip() or "Validator"
        raw_severity = str(item.get("severity", "info")).lower()
        severity = raw_severity if raw_severity in {"error", "warning", "info"} else "info"
        finding_code = _finding_code(category, message)
        records.append(
            EvidenceRecord(
                evidence_id=_evidence_id(run_id, target_url, finding_code, message, result_hash),
                run_id=run_id,
                provider="digestseo-web-validator",
                tool="audit_public_webpage",
                target_url=target_url,
                finding_code=finding_code,
                category=category,
                severity=severity,
                message=message,
                details={
                    key: value
                    for key, value in item.items()
                    if key not in {"message", "severity", "category"}
                },
                retrieved_at=timestamp,
                result_hash=result_hash,
            )
        )

    return records


class ValidatorClient:
    def __init__(self, mcp_url: str) -> None:
        self._mcp_url = mcp_url

    async def audit_page(self, url: str, run_id: str) -> list[EvidenceRecord]:
        try:
            from fastmcp import Client  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - deployed dependency
            raise RuntimeError("fastmcp is required to call the hosted validator") from exc

        async with Client(self._mcp_url) as client:
            result = await client.call_tool(
                "audit_public_webpage",
                {"url": url, "check_links": False},
            )
        data = getattr(result, "data", None)
        if not isinstance(data, dict):
            data = getattr(result, "structured_content", None)
        if not isinstance(data, dict):
            raise ValidatorProviderError("Validator returned no structured result data.")
        return normalize_validator_result(data, url, run_id)
