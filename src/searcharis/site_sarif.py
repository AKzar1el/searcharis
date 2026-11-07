"""Export deterministic SARIF for built HTML or explicitly mapped source files.

Line 1 is a document-level anchor, not an inferred offending source line.
This module only transforms an in-memory report; it performs no I/O.
"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import quote

_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
_TOOL_NAME = "Searcharis SEO Contract CI"


def _preview(value: object) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=True)
    if len(serialized) > 480:
        return serialized[:480] + "... [truncated]"
    return serialized


def _fingerprint(path: str, issue: dict, occurrence: int) -> str:
    # Keep the historical fingerprint for the first finding; distinguish
    # repeated identical findings without changing existing alert identities.
    identity = [path, issue] if occurrence == 0 else [path, issue, occurrence]
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _result(
    path: str, code: str, message: str, issue: dict, sources: dict[str, str], occurrence: int
) -> dict:
    build_location = {"uri": quote(path, safe="/"), "uriBaseId": "%BUILDROOT%"}
    source = sources.get(path)
    result = {
        "ruleId": code.split(":", 1)[0],
        "level": "error",
        "message": {"text": message},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": (
                        {"uri": quote(source, safe="/")} if source else build_location
                    ),
                    "region": {"startLine": 1},
                },
            }
        ],
        "partialFingerprints": {"searcharis/issue/v1": _fingerprint(path, issue, occurrence)},
    }
    if source:
        result["relatedLocations"] = [{
            "id": 1,
            "message": {"text": "Generated HTML artifact (document-level anchor)"},
            "physicalLocation": {
                "artifactLocation": build_location,
                "region": {"startLine": 1},
            },
        }]
    return result


def build_sarif_report(report: dict, *, sources: dict[str, str] | None = None) -> dict:
    """Convert a site_cli result; preserve its pass/fail policy and offline limits."""
    sources = sources or {}
    findings: list[tuple[str, str, dict, str]] = []
    for page in report["pages"]:
        path = page["path"]
        url = page["checked_url"]
        for issue in page["issues"]:
            code = issue["code"]
            message = (
                f"{code} for {_preview(url)}: expected {_preview(issue['expected'])}; "
                f"observed {_preview(issue['actual'])}"
            )
            if "target" in issue:
                message += f"; linked target {_preview(issue['target'])}"
            findings.append((path, code, issue, message))

    if report["coverage"]["require_full_coverage"]:
        for path in report["coverage"]["unlisted"]:
            issue = {
                "code": "unlisted_html",
                "expected": "HTML declared in the manifest",
                "actual": path,
            }
            findings.append((
                path,
                "unlisted_html",
                issue,
                f"unlisted_html: built HTML artifact {path!r} is absent from the manifest",
            ))

    findings.sort(key=lambda item: (
        item[0], item[1], json.dumps(item[2], sort_keys=True, ensure_ascii=True)
    ))
    rules = sorted({code.split(":", 1)[0] for _path, code, _issue, _message in findings})
    occurrences: dict[tuple[str, str], int] = {}
    results = []
    for path, code, issue, message in findings:
        key = (path, json.dumps(issue, sort_keys=True, separators=(",", ":")))
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        results.append(_result(path, code, message, issue, sources, occurrence))
    return {
        "$schema": _SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": _TOOL_NAME,
                        "rules": [
                            {
                                "id": code,
                                "shortDescription": {"text": code.replace("_", " ")},
                            }
                            for code in rules
                        ],
                    }
                },
                "results": results,
            }
        ],
    }
