"""Bounded reviewer Markdown for the standalone local robots.txt policy gate."""

from __future__ import annotations

from html import escape

_MAX_URLS = 16
_MAX_VALUE = 160


def _safe_code(value: object) -> str:
    # repr makes control characters visible; HTML escaping protects Markdown/HTML
    # reviewers even when the reviewed URL contains markup-like text.
    bounded = repr(value)
    if len(bounded) > _MAX_VALUE:
        bounded = bounded[:_MAX_VALUE - 3] + "..."
    return f"<code>{escape(bounded, quote=True)}</code>"


def build_current_robots_markdown(report: dict) -> str:
    """Describe current blockers, not historical regressions or live crawler behavior."""
    expected = report["expected_sha256"][:12]
    observed = (
        report["observed_sha256"][:12] if report["observed_sha256"] else "missing"
    )
    lines = [
        "# Searcharis SEO current robots",
        "",
        f"**Result: {report['status'].upper()}** (reviewed local artifacts only)",
        "",
        f"- Reviewed robots.txt SHA-256: `{expected}` (prefix)",
        f"- Built robots.txt SHA-256: `{observed}` (prefix)",
        "",
        "## Current blocking findings",
        "",
    ]

    findings = []
    if report["reason"] in ("missing", "changed"):
        findings.append(f"- Built robots.txt: {_safe_code(report['reason'])}; "
                        "its bytes differ from the reviewed policy")
    if report["reason"] == "sitemap_declaration_missing":
        missing = report.get("missing_sitemap_urls", [
            report["required_sitemap_url"],
        ])
    else:
        # Multiple reviewed URLs have explicit per-URL evidence even when the
        # built file also changed. A single URL has no explicit missing flag
        # for the changed-file case, so do not guess from required_sitemap_url.
        missing = report.get("missing_sitemap_urls", [])
    for url in missing[:_MAX_URLS]:
        findings.append(f"- Missing reviewed Sitemap declaration: {_safe_code(url)}")
    lines.extend(findings or ["No blocking robots.txt findings."])
    if len(missing) > _MAX_URLS:
        lines.append(
            f"- ... and {len(missing) - _MAX_URLS} more missing Sitemap declarations; "
            "inspect the JSON report for complete URLs"
        )

    if report["status"] == "fail":
        lines.extend([
            "",
            "## Reviewer next steps",
            "",
            "- Compare the built root robots.txt bytes with the approved local policy.",
            "- Correct the built artifact or explicitly review and update the policy.",
            "- Confirm required Sitemap declarations before rerunning this offline gate.",
        ])
    lines.extend([
        "",
        "_Offline robots.txt artifact verification only. No URLs are fetched; this "
        "does not prove live deployment, crawler access, indexing, traffic or adoption._",
    ])
    return "\n".join(lines)
