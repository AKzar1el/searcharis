"""JUnit XML serialization of only blocking new SEO build failures."""

import xml.etree.ElementTree as ET

from searcharis.site_junit import build_comparison_junit


def _comparison(**changes):
    result = {
        "new_issues": [],
        "removed_pages": [],
        "new_unlisted": [],
        "new_unverified_targets": [],
    }
    result.update(changes)
    return result


def test_junit_sanitizes_xml_invalid_control_chars_and_escapes_markup():
    report = _comparison(new_issues=[{
        "path": "café&a.html",
        "checked_url": "https://example.test/a&b",
        "issue": {
            "code": "title_presence",
            "expected": "Clear",
            "actual": "<bad&\x01text>",
            "target": "https://example.test/linked?a=1&b=2",
        },
    }])
    xml = build_comparison_junit(report)
    assert xml.isascii()
    suite = ET.fromstring(xml)
    assert suite.attrib["tests"] == "1"
    assert suite.attrib["failures"] == "1"
    assert "&amp;" in xml
    assert "&lt;" in xml
    assert "\x01" not in xml
    assert "\ufffd" in suite.find("testcase/failure").text
    assert "café&a.html" in suite.find("testcase").attrib["name"]
    assert "linked target: https://example.test/linked?a=1&b=2" in (
        suite.find("testcase/failure").text
    )


def test_junit_identical_findings_are_separate_stable_cases():
    issue = {
        "path": "about" + "x" * 350 + ".html",
        "checked_url": "https://example.test/about",
        "issue": {"code": "title_presence", "expected": True, "actual": False},
    }
    comparison = _comparison(new_issues=[issue, issue])
    first = build_comparison_junit(comparison)
    assert first == build_comparison_junit(comparison)
    suite = ET.fromstring(first)
    cases = suite.findall("testcase")
    assert suite.attrib["tests"] == "2"
    assert suite.attrib["failures"] == "2"
    assert cases[0].attrib["name"] != cases[1].attrib["name"]


def test_junit_removed_page_and_gated_new_unlisted_are_blocking():
    report = _comparison(
        removed_pages=[{"path": "old.html", "checked_url": "https://example.test/old"}],
        new_unlisted=["new.html"],
    )
    assert ET.fromstring(build_comparison_junit(report)).attrib["failures"] == "1"
    suite = ET.fromstring(build_comparison_junit(report, gate_new_unlisted=True))
    assert suite.attrib["tests"] == "2"
    assert suite.attrib["failures"] == "2"
    names = [case.attrib["name"] for case in suite.findall("testcase")]
    assert "old.html" in names[0]
    assert "new.html" in names[1]


def test_junit_unverified_targets_require_opt_in_blocking_policy():
    report = _comparison(new_unverified_targets=[{
        "path": "index.html", "check": "hreflang_clusters",
        "language": "de", "target": "https://outside.test/de",
    }])
    assert ET.fromstring(build_comparison_junit(report)).attrib["failures"] == "0"
    suite = ET.fromstring(build_comparison_junit(
        report, gate_new_unverified_targets=True
    ))
    assert suite.attrib["failures"] == "1"
    assert "hreflang_clusters_unverified_target" in (
        suite.find("testcase/failure").attrib["message"]
    )
