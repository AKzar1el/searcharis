"""Deterministic SARIF for one offline HTML, sitemap and optional robots release gate.

Sitemap findings anchor to the reviewed manifest as a whole, not an inferred
offending XML line. The serializer performs no filesystem or network I/O.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from searcharis.site_sarif import build_sarif_report

_SITEMAP_FIELDS = (
    "missing_required",
    "present_excluded",
    "present_noncanonical",
    "unreviewed_urls",
)


def _manifest_uri(manifest: Path) -> str:
    """Use a repository-relative document hint if known, never a machine path."""
    try:
        relative = manifest.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        relative = Path(manifest.name)
    return quote(relative.as_posix(), safe="/-._~")


def build_release_sarif(
    site: dict, sitemap: dict, manifest: Path, *, sources: dict[str, str] | None = None,
    robots: dict | None = None,
) -> dict:
    """Combine existing site SARIF with all gated local sitemap URL findings."""
    report = build_sarif_report(site, sources=sources)
    run = report["runs"][0]
    findings = run["results"]
    rules = {rule["id"] for rule in run["tool"]["driver"]["rules"]}
    manifest_uri = _manifest_uri(manifest)

    for field in _SITEMAP_FIELDS:
        rule_id = f"sitemap_{field}"
        for url in sorted(sitemap.get(field, [])):
            # Fingerprints include complete identities; messages are bounded to
            # avoid echoing arbitrarily long local observations into CI logs.
            identity = json.dumps(
                ["sitemap", field, url], ensure_ascii=True, separators=(",", ":"),
            )
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            shown = json.dumps(url, ensure_ascii=True)
            if len(shown) > 480:
                shown = shown[:477] + "..."
            findings.append({
                "ruleId": rule_id,
                "level": "error",
                "message": {"text": f"{field}: {shown}; review the manifest and supplied XML URL sets"},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": manifest_uri},
                        "region": {"startLine": 1},
                    },
                }],
                "partialFingerprints": {"searcharis/issue/v1": digest},
            })
            rules.add(rule_id)

    if robots is not None and robots["status"] == "fail":
        sitemap_missing = robots["reason"] == "sitemap_declaration_missing"
        rule_id = "robots_sitemap_declaration_missing" if sitemap_missing else "robots_policy_drift"
        identity_values = [
            "robots", robots["reason"], robots["expected_sha256"],
            robots["observed_sha256"],
        ]
        if sitemap_missing:
            identity_values.append(robots["required_sitemap_url"])
        identity = json.dumps(
            identity_values, separators=(",", ":"),
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        message = (
            "Reviewed robots.txt omits required Sitemap URL: "
            + json.dumps(robots["required_sitemap_url"], ensure_ascii=True)[:480]
            if sitemap_missing else
            f"Built robots.txt {robots['reason']}; "
            f"reviewed SHA-256 {robots['expected_sha256']}; "
            f"built SHA-256 {robots['observed_sha256'] or 'missing'}"
        )
        findings.append({
            "ruleId": rule_id,
            "level": "error",
            "message": {"text": message},
            "locations": [{
                "physicalLocation": {"artifactLocation": {"uri": "robots.txt"}},
            }],
            "partialFingerprints": {"searcharis/issue/v1": digest},
        })
        rules.add(rule_id)

    run["tool"]["driver"]["rules"] = [
        {"id": code, "shortDescription": {"text": code.replace("_", " ")}}
        for code in sorted(rules)
    ]
    return report
