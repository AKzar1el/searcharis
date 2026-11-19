"""Bounded, HTML-escaped reviewer summaries of local reviewed-site checks."""

from __future__ import annotations

from html import escape

_MAX_FINDINGS = 20
_MAX_UNREVIEWED_PREVIEW = 10
_MAX_VALUE = 160


def _code(value: object) -> str:
    # repr makes untrusted line breaks visible; HTML-escape before Markdown embeds.
    display = repr(value)
    if len(display) > _MAX_VALUE:
        display = display[:_MAX_VALUE - 3] + "..."
    return f"<code>{escape(display, quote=True)}</code>"


def build_site_markdown(site: dict) -> str:
    """Describe gated local page issues, without pretending to check live URLs."""
    summary = site["summary"]
    coverage = site["coverage"]
    strict = coverage["require_full_coverage"]
    unlisted = coverage["unlisted"]
    lines = [
        "# Searcharis SEO site check",
        "",
        f"**Result: {site['status'].upper()}** (supplied local artifacts only)",
        "",
        f"- **Reviewed pages:** {summary['passed']}/{summary['checked']} passed",
        f"- **Coverage:** {coverage['discovered']} built HTML; "
        f"{len(unlisted)} unlisted ({'blocking' if strict else 'not gated'})",
        "",
        "## Blocking findings",
        "",
    ]

    # Reserve room for a strict-coverage failure when page issues are numerous.
    reserve = int(bool(strict and unlisted))
    findings = []
    total = 0
    for page in site["pages"]:
        for issue in page["issues"]:
            total += 1
            if len(findings) >= _MAX_FINDINGS - reserve:
                continue
            detail = f"- Page {_code(page['path'])}: issue {_code(issue['code'])}"
            if "expected" in issue:
                detail += f"; expected {_code(issue['expected'])}"
            if "actual" in issue:
                detail += f"; observed {_code(issue['actual'])}"
            if "target" in issue:
                detail += f"; linked target {_code(issue['target'])}"
            findings.append(detail)

    if strict:
        for path in unlisted:
            total += 1
            if len(findings) < _MAX_FINDINGS:
                findings.append(f"- Unlisted built HTML: {_code(path)}")

    lines.extend(findings or ["No blocking findings in the reviewed local pages."])
    if total > len(findings):
        lines.append(
            f"- ... and {total - len(findings)} more blocking findings; "
            "use the JSON report for complete details"
        )
    if site["status"] == "fail":
        lines.extend([
            "", "## Reviewer next steps", "",
            "- Correct the built HTML or explicitly review its committed SEO manifest.",
            "- When strict coverage blocks, review omitted routes before adding them.",
        ])
    if unlisted and not strict:
        lines.extend([
            "", "## Unreviewed built HTML (not gated)", "",
            "These built pages have no reviewed SEO contract yet:",
        ])
        lines.extend(f"- {_code(path)}" for path in unlisted[:_MAX_UNREVIEWED_PREVIEW])
        omitted = len(unlisted) - _MAX_UNREVIEWED_PREVIEW
        if omitted > 0:
            lines.append(
                f"- ... and {omitted} more unreviewed paths; "
                "see the JSON report for the complete list"
            )
        lines.append(
            "Review these pages and extend the committed manifest before "
            "enabling strict coverage. A passing result does not verify them."
        )
    lines.extend([
        "", "_Offline artifact verification only. No URL is fetched; this does not "
        "verify live routing, indexing, SEO traffic or customer adoption._",
    ])
    return "\n".join(lines)
