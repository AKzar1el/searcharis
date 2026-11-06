"""Export local SEO site findings as deterministic SARIF 2.1.0.

SARIF locations refer to generated HTML artifacts, not source templates.
Line 1 is a document-level anchor, not an inferred offending HTML line.
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


def _fingerprint(path: str, issue: dict) -> str:
    raw = json.dumps([path, issue], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _result(path: str, code: str, message: str, issue: dict) -> dict:
    return {
        "ruleId": code.split(":", 1)[0],
        "level": "error",
        "message": {"text": message},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": quote(path, safe="/"),
                        "uriBaseId": "%BUILDROOT%",
                    },
                    "region": {"startLine": 1},
                },
            }
        ],
        "partialFingerprints": {"searcharis/issue/v1": _fingerprint(path, issue)},
    }


def build_sarif_report(report: dict) -> dict:
    """Convert a site_cli result; preserve its pass/fail policy and offline limits."""
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
                "results": [
                    _result(path, code, message, issue)
                    for path, code, issue, message in findings
                ],
            }
        ],
    }
