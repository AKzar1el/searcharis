"""Standalone built-site JUnit ingestion without running a combined release check."""

import json
import socket
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from searcharis.site_cli import main
from searcharis.site_junit import build_site_junit


def _inputs(tmp_path, *, fail=False, extra=False):
    build = tmp_path / "build"
    build.mkdir()
    title = "Actual" if fail else "Expected"
    (build / "index.html").write_text(
        f"<html><head><title>{title}</title></head><body></body></html>",
        encoding="utf-8",
    )
    if extra:
        (build / "unreviewed.html").write_text("<title>Unreviewed</title>", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "pages": [{
            "path": "index.html",
            "contract": {
                "url": "https://example.test/",
                "title_equals": "Expected",
            },
        }],
    }), encoding="utf-8")
    return build, manifest


def _run(manifest, build, *options):
    return main([str(manifest), str(build), *[str(part) for part in options]])


def test_junit_file_pass_is_parseable_and_no_stdout(tmp_path, capsys):
    build, manifest = _inputs(tmp_path)
    report = tmp_path / "site.xml"
    assert _run(manifest, build, "--junit-file", report) == 0
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    raw = report.read_bytes()
    assert raw.startswith(b"<?xml") and not raw.startswith(b"\xef\xbb\xbf")
    suite = ET.fromstring(raw)
    assert suite.attrib["tests"] == "1"
    assert suite.attrib["failures"] == "0"
    assert suite.find("testcase/failure") is None


def test_checked_in_bilingual_static_fixture_writes_junit_without_network(
    tmp_path, monkeypatch,
):
    def deny_network(*_args, **_kwargs):
        raise AssertionError("Offline SEO CI attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    repo = Path(__file__).resolve().parents[2]
    sample = repo / "examples" / "seo-contract"
    report = tmp_path / "bilingual.xml"
    assert _run(
        sample / "bilingual-manifest.json", sample / "bilingual-build",
        "--require-full-coverage", "--junit-file", report,
    ) == 0
    assert ET.fromstring(report.read_bytes()).attrib["failures"] == "0"


def test_junit_file_contains_actionable_blocking_page_issue(tmp_path, capsys):
    build, manifest = _inputs(tmp_path, fail=True)
    report = tmp_path / "site.xml"
    assert _run(manifest, build, "--junit-file", report, "--github-annotations") == 1
    assert "::error" in capsys.readouterr().out
    suite = ET.fromstring(report.read_bytes())
    assert suite.attrib["tests"] == suite.attrib["failures"] == "1"
    case = suite.find("testcase")
    assert "title_equals" in case.attrib["name"]
    assert "index.html" in case.find("failure").text
    assert "Expected" in case.find("failure").text


def test_junit_strict_coverage_gates_unlisted_html_only_when_enabled(tmp_path):
    build, manifest = _inputs(tmp_path, extra=True)
    normal = tmp_path / "normal.xml"
    strict = tmp_path / "strict.xml"
    assert _run(manifest, build, "--junit-file", normal) == 0
    assert ET.fromstring(normal.read_bytes()).attrib["failures"] == "0"
    assert _run(manifest, build, "--require-full-coverage", "--junit-file", strict) == 1
    failure = ET.fromstring(strict.read_bytes()).find("testcase")
    assert "unlisted_html" in failure.attrib["name"]
    assert "unreviewed.html" in failure.find("failure").text


def test_site_junit_escapes_xml_invalid_text_and_keeps_duplicate_cases_distinct():
    issue = {"code": "title_mismatch", "expected": "<bad&\x01>", "actual": "actual"}
    site = {
        "pages": [{"path": "café&.html", "checked_url": "https://example.test/",
                   "issues": [issue, issue]}],
        "coverage": {"require_full_coverage": False, "unlisted": ["ignored.html"]},
    }
    xml = build_site_junit(site)
    assert build_site_junit(site) == xml and xml.isascii()
    assert "\x01" not in xml
    suite = ET.fromstring(xml)
    assert suite.attrib["failures"] == "2"
    cases = suite.findall("testcase")
    assert cases[0].attrib["name"] != cases[1].attrib["name"]
    assert "<bad&\ufffd>" in cases[0].find("failure").text


@pytest.mark.parametrize("error", ["existing", "missing-parent", "in-build", "invalid-input"])
def test_junit_file_preflight_never_overwrites_or_mutates_inputs(tmp_path, capsys, error):
    build, manifest = _inputs(tmp_path)
    target = tmp_path / "site.xml"
    if error == "existing":
        target.write_text("owned by another job", encoding="utf-8")
    elif error == "missing-parent":
        target = tmp_path / "missing" / "site.xml"
    elif error == "in-build":
        target = build / "site.xml"
    else:
        manifest.write_text("{invalid", encoding="utf-8")
    assert _run(manifest, build, "--junit-file", target, "--github-annotations") == 2
    assert capsys.readouterr().out == ""
    if error == "existing":
        assert target.read_text(encoding="utf-8") == "owned by another job"
    else:
        assert not target.exists()


@pytest.mark.parametrize("conflict", ["--json", "--sarif", "--json-file", "--sarif-file"])
def test_junit_file_is_exclusive_with_existing_report_modes(tmp_path, conflict):
    build, manifest = _inputs(tmp_path)
    arguments = ["--junit-file", str(tmp_path / "site.xml"), conflict]
    if conflict.endswith("file"):
        arguments.append(str(tmp_path / "other.json"))
    with pytest.raises(SystemExit) as error:
        _run(manifest, build, *arguments)
    assert error.value.code == 2
    assert not (tmp_path / "site.xml").exists()
