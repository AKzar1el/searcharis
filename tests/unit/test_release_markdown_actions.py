"""Reviewer-facing remediation hints in offline release Markdown artifacts."""

from searcharis.release_markdown import build_release_markdown


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
