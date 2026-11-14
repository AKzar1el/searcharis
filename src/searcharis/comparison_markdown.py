"""Human-readable, bounded review of new offline build regressions.

This report is advisory: compare_reports owns the blocking decision.
"""

from __future__ import annotations

from html import escape

_MAX_FINDINGS = 20
_MAX_VALUE = 160


def _code(value: object) -> str:
    # repr escapes line breaks and controls before HTML escaping prevents
    # untrusted build-derived text from becoming Markdown markup.
    display = repr(value)
    if len(display) > _MAX_VALUE:
        display = display[:_MAX_VALUE - 3] + "..."
    return f"<code>{escape(display, quote=True)}</code>"


def build_comparison_markdown(
    result: dict, *, gate_new_unlisted: bool = False,
    gate_new_unverified_targets: bool = False,
) -> str:
    """Summarize one validated comparison, distinguishing optional gates."""
    summary = result["summary"]
    lines = [
        "# Searcharis SEO build comparison",
        "",
        f"**Result: {result['status'].upper()}** (reviewed local artifacts only)",
        "",
        f"- New page issues: {summary['new_issues']} (blocking)",
        f"- Removed reviewed pages: {summary['removed_pages']} (blocking)",
        f"- New unlisted built HTML: {summary['new_unlisted']} "
        f"({'blocking' if gate_new_unlisted else 'not gated'})",
        f"- New unverified cross-page targets: {summary['new_unverified_targets']} "
        f"({'blocking' if gate_new_unverified_targets else 'not gated'})",
        f"- Inherited baseline issues: {summary['existing_issues']} (not blocking)",
        f"- Resolved issues: {summary['resolved_issues']}",
        "",
        "## New findings",
        "",
    ]
    issue_lines = []
    for item in result["new_issues"][:_MAX_FINDINGS]:
        issue = item["issue"]
        issue_lines.append(
            f"- Page {_code(item['path'])}: issue {_code(issue['code'])}; "
            f"expected {_code(issue['expected'])}; observed {_code(issue['actual'])}"
        )
    removed_lines = [
        f"- Removed reviewed page: {_code(item['path'])}"
        for item in result["removed_pages"][:_MAX_FINDINGS]
    ]
    unlisted_policy = "blocking" if gate_new_unlisted else "not gated"
    unlisted_lines = [
        f"- New unlisted HTML ({unlisted_policy}): {_code(path)}"
        for path in result["new_unlisted"][:_MAX_FINDINGS]
    ]
    target_policy = "blocking" if gate_new_unverified_targets else "not gated"
    target_lines = []
    for item in result["new_unverified_targets"][:_MAX_FINDINGS]:
        language = f"; locale {_code(item['language'])}" if "language" in item else ""
        target_lines.append(
            f"- New unverified target ({target_policy}): {_code(item['check'])} "
            f"from {_code(item['path'])}{language} to {_code(item['target'])}"
        )

    groups = (issue_lines, removed_lines, unlisted_lines, target_lines)
    findings = []
    remaining = _MAX_FINDINGS
    for index, group in enumerate(groups):
        # Reserve one slot for every later non-empty category before filling
        # this category; a flood of page issues must not hide removed routes.
        reserved = sum(bool(later) for later in groups[index + 1:])
        take = min(len(group), max(0, remaining - reserved))
        findings.extend(group[:take])
        remaining -= take
    lines.extend(findings or ["No new blocking findings or observations."])
    total_findings = sum(len(result[key]) for key in (
        "new_issues", "removed_pages", "new_unlisted", "new_unverified_targets",
    ))
    if total_findings > len(findings):
        lines.append(
            f"- ... and {total_findings - len(findings)} more new observations; "
            "rerun with --json for complete findings"
        )

    if result["status"] == "regression":
        lines.extend([
            "",
            "## Reviewer next steps",
            "",
            "- Inspect newly blocking findings and the reviewed contract or manifest.",
            "- Keep inherited baseline debt separate from this release's blockers.",
            "- Fix the candidate artifact or explicitly review the policy before rerunning.",
        ])
    lines.extend([
        "",
        "_Offline artifact comparison only. It does not fetch URLs or verify live "
        "routing, Google indexing, SEO traffic or customer adoption._",
    ])
    return "\n".join(lines)
