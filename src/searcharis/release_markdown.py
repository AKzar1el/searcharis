"""Create bounded, human-readable offline SEO release summaries for CI artifacts.

Only already-computed local verifier results are rendered. Untrusted paths,
URLs and issue details are never treated as Markdown or raw HTML.
"""

from __future__ import annotations

from html import escape

_MAX_FINDINGS = 20
_MAX_VALUE = 160
_MAX_ACTIONS = 10

# Fixed reviewer guidance: never interpolate values from an untrusted contract.
_HTML_ACTIONS = (
    ("title_presence", "Add a non-empty HTML title to each required page."),
    ("title_duplicate", "Give reviewed indexable pages distinct HTML titles."),
    ("description_presence", "Add a non-empty head meta description where required."),
    ("description_duplicate", "Write page-specific meta descriptions for reviewed indexable pages."),
    ("navigation_target_unreviewed",
     "Correct stale navigation links or review the complete route manifest."),
    ("navigation_fragment_missing",
     "Correct the fragment or add its matching HTML target."),
    ("navigation_fragment_ambiguous",
     "Make linked HTML fragment targets unambiguous in the built page."),
)
_SITEMAP_ACTIONS = (
    ("missing_required",
     "Add missing reviewed canonical URLs to the supplied sitemap, or correct the expected set."),
    ("present_excluded",
     "Remove explicitly excluded URLs from the supplied sitemap, or review the exclusion."),
    ("present_noncanonical",
     "Review canonical targets and remove noncanonical URLs from the supplied sitemap."),
    ("unreviewed_urls",
     "Review unexpected sitemap URLs against the complete site manifest."),
)


def _code(value: object) -> str:
    # repr escapes control/newline/lone-surrogate characters before HTML
    # escaping. HTML <code> prevents URL/path text becoming Markdown syntax.
    rendered = repr(value)
    if len(rendered) > _MAX_VALUE:
        rendered = rendered[:_MAX_VALUE - 3] + "..."
    return f"<code>{escape(rendered, quote=True)}</code>"


def build_release_markdown(site: dict, sitemap: dict) -> str:
    """Render actionable, bounded findings without claiming live SEO behavior."""
    summary = site["summary"]
    xml = sitemap["summary"]
    passed = site["status"] == sitemap["status"] == "pass"
    lines = [
        "# Searcharis SEO release check",
        "",
        f"**Result: {'PASS' if passed else 'FAIL'}** (supplied local artifacts only)",
        "",
        f"- **HTML:** {site['status'].upper()} - "
        f"{summary['passed']}/{summary['checked']} reviewed pages passed",
        f"- **Sitemap:** {sitemap['status'].upper()} - "
        f"{xml['required']} required, {xml['excluded']} excluded, "
        f"{xml['sitemap_urls']} URLs examined",
    ]

    coverage = site["coverage"]
    if coverage["unlisted"]:
        policy = "blocking" if coverage["require_full_coverage"] else "not gated"
        lines.append(
            f"- **Unlisted built HTML:** {len(coverage['unlisted'])} ({policy})"
        )

    html_items: list[str] = []
    html_count = 0
    # Reserve a coverage example when strict coverage is enabled; otherwise
    # numerous page issues could hide the first unlisted built HTML path.
    issue_limit = _MAX_FINDINGS - int(bool(
        coverage["require_full_coverage"] and coverage["unlisted"]
    ))
    for page in site["pages"]:
        for issue in page["issues"]:
            html_count += 1
            if len(html_items) >= issue_limit:
                continue
            item = f"- Page {_code(page['path'])}: issue {_code(issue['code'])}"
            if "expected" in issue:
                item += f"; expected {_code(issue['expected'])}"
            if "actual" in issue:
                item += f"; observed {_code(issue['actual'])}"
            if "target" in issue:
                item += f"; linked target {_code(issue['target'])}"
            html_items.append(item)

    if coverage["require_full_coverage"]:
        for path in coverage["unlisted"]:
            html_count += 1
            if len(html_items) < _MAX_FINDINGS:
                html_items.append(f"- Unlisted built HTML: {_code(path)}")

    lines.extend(["", "## Blocking HTML findings", ""])
    lines.extend(html_items or ["None in the reviewed local pages."])
    if html_count > len(html_items):
        lines.append(f"- ... and {html_count - len(html_items)} more HTML findings")

    sitemap_items: list[str] = []
    sitemap_count = 0
    sitemap_groups = (
        ("missing_required", "Missing required canonical URL"),
        ("present_excluded", "Reviewed noindex URL unexpectedly present"),
        ("present_noncanonical", "Reviewed noncanonical URL unexpectedly present"),
        ("unreviewed_urls", "Unreviewed URL unexpectedly present"),
    )
    for index, (key, label) in enumerate(sitemap_groups):
        urls = sitemap.get(key, [])
        sitemap_count += len(urls)
        # Keep one actionable example for each later nonempty sitemap category
        # without increasing the bounded report size.
        reserved = sum(bool(sitemap.get(next_key)) for next_key, _ in sitemap_groups[index + 1:])
        remaining = _MAX_FINDINGS - len(sitemap_items) - reserved
        for url in urls[:max(0, remaining)]:
            sitemap_items.append(f"- {label}: {_code(url)}")

    lines.extend(["", "## Blocking sitemap findings", ""])
    lines.extend(sitemap_items or ["None in the supplied local URL sets."])
    if sitemap_count > len(sitemap_items):
        lines.append(
            f"- ... and {sitemap_count - len(sitemap_items)} more sitemap findings"
        )

    if not passed:
        issue_codes = {
            issue["code"]
            for page in site["pages"]
            for issue in page["issues"]
            if isinstance(issue.get("code"), str)
        }
        actions = [hint for code, hint in _HTML_ACTIONS if code in issue_codes]
        if coverage["require_full_coverage"] and coverage["unlisted"]:
            actions.append(
                "Add missing built HTML pages to the reviewed manifest, "
                "or correct the declared coverage policy."
            )
        actions.extend(
            hint for key, hint in _SITEMAP_ACTIONS if sitemap.get(key)
        )
        lines.extend(["", "## Suggested reviewer actions", ""])
        lines.extend(f"- {hint}" for hint in actions[:_MAX_ACTIONS])
        if len(actions) > _MAX_ACTIONS:
            lines.append(f"- ... and {len(actions) - _MAX_ACTIONS} more action categories")
        if not actions:
            lines.append("- Review the issue codes above against the local manifest and built files.")
        lines.append(
            "- No live URL was checked; verify public routing and intended SEO "
            "policy separately."
        )

    lines.extend([
        "",
        "_Offline artifact check only. It does not fetch URLs or verify live "
        "hosting, HTTP headers, crawler indexing or search traffic._",
    ])
    return "\n".join(lines)
