"""JUnit XML output for new, blocking local SEO build regressions.

A passing suite means the candidate adds no new gated failures, not that every
baseline page is SEO-clean. No files or network resources are opened here.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET


def _xml_safe(text: str) -> str:
    """Replace characters XML 1.0 forbids, including lone surrogates."""
    return "".join(
        character if (
            ord(character) in (0x9, 0xA, 0xD)
            or 0x20 <= ord(character) <= 0xD7FF
            or 0xE000 <= ord(character) <= 0xFFFD
            or 0x10000 <= ord(character) <= 0x10FFFF
        ) else "\uFFFD"
        for character in text
    )


def _bounded(value: object, *, limit: int = 480) -> str:
    rendered = value if isinstance(value, str) else json.dumps(
        value, sort_keys=True, ensure_ascii=True
    )
    cleaned = _xml_safe(rendered)
    return cleaned if len(cleaned) <= limit else cleaned[:limit - 3] + "..."


def build_comparison_junit(
    comparison: dict, *, gate_new_unlisted: bool = False,
    gate_new_unverified_targets: bool = False,
) -> str:
    """Represent each newly blocking issue as one deterministic JUnit testcase."""
    failures: list[tuple[str, str, str]] = []

    for item in comparison["new_issues"]:
        code = item["issue"]["code"]
        issue = item["issue"]
        details = (
            f"Built page: {_bounded(item['path'])}; URL: {_bounded(item['checked_url'])}; "
            f"expected: {_bounded(issue['expected'])}; observed: {_bounded(issue['actual'])}"
        )
        if "target" in issue:
            details += f"; linked target: {_bounded(issue['target'])}"
        failures.append((
            f"{item['path']} :: {code}",
            f"New SEO regression: {code}",
            details,
        ))

    for item in comparison["removed_pages"]:
        failures.append((
            f"{item['path']} :: removed_page",
            "Removed manifest page",
            f"Missing page: {_bounded(item['path'])}; URL: {_bounded(item['checked_url'])}",
        ))

    if gate_new_unlisted:
        for path in comparison["new_unlisted"]:
            failures.append((
                f"{path} :: unlisted_html",
                "New unlisted HTML artifact",
                f"HTML absent from reviewed manifest: {_bounded(path)}",
            ))

    if gate_new_unverified_targets:
        for item in comparison["new_unverified_targets"]:
            code = f"{item['check']}_unverified_target"
            language = f"; language: {_bounded(item['language'])}" if "language" in item else ""
            failures.append((
                f"{item['path']} :: {code}",
                f"New unverified target: {code}",
                (
                    f"Built page: {_bounded(item['path'])}; target: {_bounded(item['target'])}"
                    f"{language}"
                ),
            ))

    count = len(failures)
    suite = ET.Element(
        "testsuite",
        name="Searcharis SEO build regression gate",
        tests=str(count or 1),
        failures=str(count),
        errors="0",
        skipped="0",
    )
    if not failures:
        ET.SubElement(
            suite, "testcase", classname="searcharis.seo",
            name="no-new-blocking-regressions",
        )
    else:
        for index, (name, message, details) in enumerate(failures, start=1):
            case = ET.SubElement(
                suite, "testcase", classname="searcharis.seo",
                # Keep the ordinal before any truncation, so distinct repeated
                # findings cannot acquire identical long testcase names.
                name=_bounded(f"[{index}] {name}", limit=300),
            )
            failure = ET.SubElement(case, "failure", message=_bounded(message))
            failure.text = _bounded(details, limit=1100)

    # Numeric character references keep CLI stdout valid on Windows terminals
    # with a non-UTF-8 code page, while XML parsers recover the original text.
    return ET.tostring(
        suite, encoding="us-ascii", xml_declaration=True,
        short_empty_elements=True,
    ).decode("ascii")


def build_release_junit(site: dict, sitemap: dict) -> str:
    """Serialize blocking HTML and sitemap findings in one offline JUnit suite."""
    failures: list[tuple[str, str, str]] = []
    for page in site["pages"]:
        for issue in page["issues"]:
            code = issue["code"]
            detail = (
                f"Built page: {_bounded(page['path'])}; "
                f"URL: {_bounded(page['checked_url'])}; "
                f"expected: {_bounded(issue.get('expected'))}; "
                f"observed: {_bounded(issue.get('actual'))}"
            )
            if "target" in issue:
                detail += f"; linked target: {_bounded(issue['target'])}"
            failures.append((
                f"html :: {page['path']} :: {code}",
                f"HTML contract mismatch: {code}",
                detail,
            ))

    if site["coverage"]["require_full_coverage"]:
        for path in site["coverage"]["unlisted"]:
            failures.append((
                f"html :: {path} :: unlisted_html",
                "Unlisted HTML artifact",
                f"Built HTML missing from reviewed manifest: {_bounded(path)}",
            ))

    for field, title in (
        ("missing_required", "Required canonical URL missing from sitemap"),
        ("present_excluded", "Reviewed noindex URL present in sitemap"),
        ("present_noncanonical", "Reviewed noncanonical URL present in sitemap"),
        ("unreviewed_urls", "Unreviewed URL present in sitemap"),
    ):
        for url in sitemap.get(field, []):
            failures.append((
                f"sitemap :: {field} :: {url}", title,
                f"Sitemap finding: {field}; URL: {_bounded(url)}",
            ))

    count = len(failures)
    suite = ET.Element(
        "testsuite", name="Searcharis SEO HTML and sitemap release gate",
        tests=str(count or 1), failures=str(count), errors="0", skipped="0",
    )
    if not count:
        ET.SubElement(suite, "testcase", classname="searcharis.release",
                      name="html-and-sitemap-passed")
    else:
        for index, (name, message, details) in enumerate(failures, start=1):
            case = ET.SubElement(
                suite, "testcase", classname="searcharis.release",
                name=_bounded(f"[{index}] {name}", limit=300),
            )
            failure = ET.SubElement(case, "failure", message=_bounded(message))
            failure.text = _bounded(details, limit=1100)

    return ET.tostring(
        suite, encoding="us-ascii", xml_declaration=True,
        short_empty_elements=True,
    ).decode("ascii")
