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


def build_site_junit(site: dict) -> str:
    """Serialize only blocking reviewed-page and strict-coverage findings."""
    failures: list[tuple[str, str, str]] = []
    for page in site["pages"]:
        for issue in page["issues"]:
            details = (
                f"Built page: {_bounded(page['path'])}; "
                f"URL: {_bounded(page['checked_url'])}; "
                f"expected: {_bounded(issue.get('expected'))}; "
                f"observed: {_bounded(issue.get('actual'))}"
            )
            if "target" in issue:
                details += f"; linked target: {_bounded(issue['target'])}"
            failures.append((
                f"{page['path']} :: {issue['code']}",
                f"HTML contract mismatch: {issue['code']}",
                details,
            ))
    if site["coverage"]["require_full_coverage"]:
        for path in site["coverage"]["unlisted"]:
            failures.append((
                f"{path} :: unlisted_html",
                "Unlisted HTML artifact",
                f"Built HTML missing from reviewed manifest: {_bounded(path)}",
            ))

    suite = ET.Element(
        "testsuite", name="Searcharis SEO reviewed-site gate",
        tests=str(len(failures) or 1), failures=str(len(failures)),
        errors="0", skipped="0",
    )
    if not failures:
        ET.SubElement(
            suite, "testcase", classname="searcharis.site", name="reviewed-site-passed",
        )
    else:
        for index, (name, message, details) in enumerate(failures, start=1):
            case = ET.SubElement(
                suite, "testcase", classname="searcharis.site",
                name=_bounded(f"[{index}] {name}", limit=300),
            )
            ET.SubElement(case, "failure", message=_bounded(message)).text = (
                _bounded(details, limit=1100)
            )
    return ET.tostring(
        suite, encoding="us-ascii", xml_declaration=True,
        short_empty_elements=True,
    ).decode("ascii")


def build_current_sitemap_junit(sitemap: dict) -> str:
    """Represent each current, blocking reviewed sitemap URL as one CI testcase."""
    failures = [
        (field, label, url)
        for field, label in (
            ("missing_required", "Required canonical URL missing"),
            ("present_excluded", "Reviewed noindex URL present"),
            ("present_noncanonical", "Reviewed noncanonical URL present"),
            ("unreviewed_urls", "Unreviewed sitemap URL present"),
        )
        for url in sitemap.get(field, [])
    ]
    suite = ET.Element(
        "testsuite", name="Searcharis SEO current sitemap gate",
        tests=str(len(failures) or 1), failures=str(len(failures)),
        errors="0", skipped="0",
    )
    if not failures:
        ET.SubElement(
            suite, "testcase", classname="searcharis.sitemap",
            name="current-sitemap-passed",
        )
    else:
        for index, (field, label, url) in enumerate(failures, start=1):
            case = ET.SubElement(
                suite, "testcase", classname="searcharis.sitemap",
                name=_bounded(f"[{index}] {field} :: {url}", limit=300),
            )
            ET.SubElement(case, "failure", message=label).text = (
                f"Sitemap finding: {field}; URL: {_bounded(url, limit=1000)}"
            )
    return ET.tostring(
        suite, encoding="us-ascii", xml_declaration=True,
        short_empty_elements=True,
    ).decode("ascii")


def build_current_robots_junit(robots: dict) -> str:
    """Serialize current standalone robots policy failures for CI ingestion."""
    failures: list[tuple[str, str, str]] = []
    if robots["status"] == "fail":
        if robots["reason"] == "sitemap_declaration_missing":
            missing = robots.get("missing_sitemap_urls") or [robots["required_sitemap_url"]]
            failures.extend((
                f"required_sitemap_missing :: {url}",
                "Reviewed robots.txt lacks required Sitemap URL",
                f"Missing reviewed Sitemap: {_bounded(url, limit=1000)}",
            ) for url in missing)
        else:
            failures.append((
                f"robots_policy_{robots['reason']}",
                "Built robots.txt differs from reviewed policy",
                f"Reason: {_bounded(robots['reason'])}; "
                f"reviewed SHA-256: {robots['expected_sha256']}; "
                f"built SHA-256: {robots['observed_sha256'] or 'missing'}",
            ))
    suite = ET.Element(
        "testsuite", name="Searcharis SEO current robots policy gate",
        tests=str(len(failures) or 1), failures=str(len(failures)),
        errors="0", skipped="0",
    )
    if not failures:
        ET.SubElement(
            suite, "testcase", classname="searcharis.robots",
            name="current-robots-policy-passed",
        )
    else:
        for index, (name, message, details) in enumerate(failures, start=1):
            case = ET.SubElement(
                suite, "testcase", classname="searcharis.robots",
                name=_bounded(f"[{index}] {name}", limit=300),
            )
            ET.SubElement(case, "failure", message=_bounded(message)).text = (
                _bounded(details, limit=1100)
            )
    return ET.tostring(
        suite, encoding="us-ascii", xml_declaration=True,
        short_empty_elements=True,
    ).decode("ascii")


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


def build_sitemap_comparison_junit(comparison: dict) -> str:
    """Serialize only newly blocking sitemap differences for CI test reports."""
    failures = [
        (category, url)
        for category, urls in comparison["new"].items()
        for url in urls
    ]
    suite = ET.Element(
        "testsuite", name="Searcharis SEO sitemap regression gate",
        tests=str(len(failures) or 1), failures=str(len(failures)),
        errors="0", skipped="0",
    )
    if not failures:
        ET.SubElement(
            suite, "testcase", classname="searcharis.sitemap",
            name="no-new-blocking-sitemap-regressions",
        )
    else:
        for index, (category, url) in enumerate(failures, start=1):
            case = ET.SubElement(
                suite, "testcase", classname="searcharis.sitemap",
                name=_bounded(f"[{index}] {category} :: {url}", limit=300),
            )
            failure = ET.SubElement(
                case, "failure",
                message=_bounded(f"New sitemap regression: {category}"),
            )
            failure.text = f"Sitemap finding: {category}; URL: {_bounded(url, limit=1000)}"
    return ET.tostring(
        suite, encoding="us-ascii", xml_declaration=True,
        short_empty_elements=True,
    ).decode("ascii")


def build_release_junit(site: dict, sitemap: dict, robots: dict | None = None) -> str:
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

    if robots is not None and robots["status"] == "fail":
        sitemap_missing = robots["reason"] == "sitemap_declaration_missing"
        missing_urls = robots.get("missing_sitemap_urls", [robots["required_sitemap_url"]]) if sitemap_missing else []
        missing_message = ", ".join(_bounded(url) for url in missing_urls[:3])
        if len(missing_urls) > 3:
            missing_message += f" (and {len(missing_urls) - 3} more missing Sitemap URLs)"
        failures.append((
            "robots.txt :: robots_sitemap_declaration_missing" if sitemap_missing
            else "robots.txt :: robots_policy_drift",
            "Reviewed robots.txt lacks required Sitemap URL" if sitemap_missing
            else "Built robots.txt differs from reviewed local policy",
            f"Expected Sitemap URL: {missing_message}"
            if sitemap_missing else
            f"Reason: {_bounded(robots['reason'])}; "
            f"reviewed SHA-256: {robots['expected_sha256']}; "
            f"built SHA-256: {robots['observed_sha256'] or 'missing'}",
        ))

    count = len(failures)
    suite = ET.Element(
        "testsuite", name=(
            "Searcharis SEO HTML, sitemap and robots.txt release gate"
            if robots is not None else "Searcharis SEO HTML and sitemap release gate"
        ),
        tests=str(count or 1), failures=str(count), errors="0", skipped="0",
    )
    if not count:
        ET.SubElement(suite, "testcase", classname="searcharis.release",
                      name=(
                          "html-sitemap-and-robots-passed"
                          if robots is not None else "html-and-sitemap-passed"
                      ))
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
