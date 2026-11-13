"""Create bounded, human-readable offline SEO release summaries for CI artifacts.

Only already-computed local verifier results are rendered. Untrusted paths,
URLs and issue details are never treated as Markdown or raw HTML.
"""

from __future__ import annotations

from html import escape

_MAX_FINDINGS = 20
_MAX_VALUE = 160


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
    for page in site["pages"]:
        for issue in page["issues"]:
            html_count += 1
            if len(html_items) >= _MAX_FINDINGS:
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
    for key, label in (
        ("missing_required", "Missing required canonical URL"),
        ("present_excluded", "Reviewed noindex URL unexpectedly present"),
        ("present_noncanonical", "Reviewed noncanonical URL unexpectedly present"),
        ("unreviewed_urls", "Unreviewed URL unexpectedly present"),
    ):
        urls = sitemap.get(key, [])
        sitemap_count += len(urls)
        remaining = _MAX_FINDINGS - len(sitemap_items)
        for url in urls[:max(0, remaining)]:
            sitemap_items.append(f"- {label}: {_code(url)}")

    lines.extend(["", "## Blocking sitemap findings", ""])
    lines.extend(sitemap_items or ["None in the supplied local URL sets."])
    if sitemap_count > len(sitemap_items):
        lines.append(
            f"- ... and {sitemap_count - len(sitemap_items)} more sitemap findings"
        )

    lines.extend([
        "",
        "_Offline artifact check only. It does not fetch URLs or verify live "
        "hosting, HTTP headers, crawler indexing or search traffic._",
    ])
    return "\n".join(lines)
