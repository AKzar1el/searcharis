"""Reviewer-facing remediation hints in offline release Markdown artifacts."""

import pytest

from searcharis.release_markdown import build_release_markdown
from searcharis.robots_policy_cli import verify_reviewed_robots_policy


def _results(*codes: str, missing_sitemap: bool = False, unlisted: bool = False):
    issues = [{"code": code} for code in codes]
    site = {
        "status": "fail" if (issues or unlisted) else "pass",
        "summary": {
            "checked": 1,
            "passed": 0 if issues else 1,
            "failed": 1 if issues else 0,
        },
        "pages": [{"path": "index.html", "issues": issues}],
        "coverage": {
            "require_full_coverage": unlisted,
            "unlisted": ["forgotten.html"] if unlisted else [],
        },
    }
    sitemap = {
        "status": "fail" if missing_sitemap else "pass",
        "summary": {"required": 1, "excluded": 0, "sitemap_urls": 1},
        "missing_required": ["https://example.test/"] if missing_sitemap else [],
        "present_excluded": [],
        "present_noncanonical": [],
        "unreviewed_urls": [],
    }
    return site, sitemap


def test_failing_markdown_explains_common_page_repairs_once():
    site, sitemap = _results(
        "title_duplicate", "title_duplicate", "description_duplicate",
        "navigation_target_unreviewed", "navigation_fragment_missing",
    )
    result = build_release_markdown(site, sitemap)
    assert "## Suggested reviewer actions" in result
    assert result.count("Give reviewed indexable pages distinct HTML titles") == 1
    assert "Write page-specific meta descriptions" in result
    assert "Correct stale navigation links or review the complete route manifest" in result
    assert "Correct the fragment or add its matching HTML target" in result
    assert result.index("## Suggested reviewer actions") > result.index("## Blocking sitemap findings")


def test_sitemap_and_coverage_failures_have_specific_local_actions():
    site, sitemap = _results(missing_sitemap=True, unlisted=True)
    result = build_release_markdown(site, sitemap)
    assert "Add missing reviewed canonical URLs to the supplied sitemap" in result
    assert "Add missing built HTML pages to the reviewed manifest" in result
    assert "No live URL was checked" in result


def test_passing_markdown_avoids_unnecessary_remediation():
    site, sitemap = _results()
    result = build_release_markdown(site, sitemap)
    assert "**Result: PASS**" in result
    assert "## Suggested reviewer actions" not in result


def test_remediation_hints_are_bounded_and_never_echo_untrusted_issue_code():
    hostile = "<script>alert(1)</script>\nmalicious item"
    codes = ["title_duplicate"] * 60 + [hostile] * 60
    site, sitemap = _results(*codes)
    result = build_release_markdown(site, sitemap)
    assert result == build_release_markdown(site, sitemap)
    assert result.count("Give reviewed indexable pages distinct HTML titles") == 1
    assert "## Suggested reviewer actions" in result
    assert "<script>" not in result
    assert len(result) < 10000


def test_bounded_html_findings_keep_one_required_unlisted_page():
    site, sitemap = _results(*(["title_duplicate"] * 40), unlisted=True)
    result = build_release_markdown(site, sitemap)
    assert "- Unlisted built HTML: <code>&#x27;forgotten.html&#x27;</code>" in result
    assert "21 more HTML findings" in result
    assert sum(line.startswith("- Page ") or line.startswith("- Unlisted built HTML:")
               for line in result.splitlines()) == 20


def test_bounded_sitemap_findings_preserve_each_blocking_category():
    site, sitemap = _results(missing_sitemap=True)
    sitemap["missing_required"] = [
        f"https://example.test/missing-{i}" for i in range(40)
    ]
    sitemap["present_excluded"] = ["https://example.test/noindex"]
    sitemap["present_noncanonical"] = ["https://example.test/alias"]
    sitemap["unreviewed_urls"] = ["https://example.test/unreviewed"]

    result = build_release_markdown(site, sitemap)
    assert "Reviewed noindex URL unexpectedly present" in result
    assert "Reviewed noncanonical URL unexpectedly present" in result
    assert "Unreviewed URL unexpectedly present" in result
    assert "23 more sitemap findings" in result
    assert sum(line.startswith("- ") for line in result.split("## Blocking sitemap findings")[1].split("## Suggested reviewer actions")[0].splitlines()) == 21


@pytest.mark.parametrize("mode", ["changed", "missing"])
def test_missing_robots_sitemaps_remain_actionable_when_built_artifact_fails(tmp_path, mode):
    build = tmp_path / "dist"
    build.mkdir()
    reviewed = tmp_path / "reviewed-robots.txt"
    reviewed.write_bytes(b"User-agent: *\nAllow: /\n")
    if mode == "changed":
        (build / "robots.txt").write_bytes(b"User-agent: *\nDisallow: /\n")
    urls = [
        "https://example.test/a.xml?x=1&y=2",
        "https://example.test/b.xml",
        "https://example.test/c.xml",
        "https://example.test/d.xml",
    ]
    robots = verify_reviewed_robots_policy(reviewed, build, required_sitemap_url=urls)
    assert robots["reason"] == mode
    assert robots["missing_sitemap_urls"] == urls

    site, sitemap = _results()
    result = build_release_markdown(site, sitemap, robots)
    assert "**Result: FAIL**" in result
    assert f"&#x27;{mode}&#x27;" in result
    assert "https://example.test/a.xml?x=1&amp;y=2" in result
    assert urls[1] in result and urls[2] in result
    assert urls[3] not in result
    assert result.count("- Required Sitemap URL:") == 3
    assert "1 more missing Sitemap URLs" in result


@pytest.mark.parametrize("mode", ["changed", "missing"])
def test_legacy_single_sitemap_is_not_inferred_missing_after_artifact_failure(tmp_path, mode):
    build = tmp_path / "dist"
    build.mkdir()
    reviewed = tmp_path / "reviewed-robots.txt"
    url = "https://example.test/present.xml"
    reviewed.write_text(f"User-agent: *\nAllow: /\nSitemap: {url}\n", encoding="utf-8")
    if mode == "changed":
        (build / "robots.txt").write_bytes(b"User-agent: *\nDisallow: /\n")
    robots = verify_reviewed_robots_policy(reviewed, build, required_sitemap_url=url)
    assert robots["reason"] == mode
    assert "missing_sitemap_urls" not in robots

    site, sitemap = _results()
    result = build_release_markdown(site, sitemap, robots)
    assert "**Result: FAIL**" in result
    assert "Required Sitemap URL:" not in result
    assert url not in result
