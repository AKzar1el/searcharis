"""Opt-in local Cloudflare Pages policy-size guard acceptance tests."""

import json
import socket
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from searcharis.pages_policy_cli import main


def _policies(tmp_path: Path, redirects: str, headers: str) -> tuple[Path, Path, Path]:
    build = tmp_path / "dist"
    build.mkdir()
    reviewed_redirects = tmp_path / "reviewed-redirects"
    reviewed_headers = tmp_path / "reviewed-headers"
    reviewed_redirects.write_text(redirects, encoding="utf-8")
    reviewed_headers.write_text(headers, encoding="utf-8")
    (build / "_redirects").write_bytes(reviewed_redirects.read_bytes())
    (build / "_headers").write_bytes(reviewed_headers.read_bytes())
    return reviewed_redirects, reviewed_headers, build


def _run(capsys, paths: tuple[Path, Path, Path], *options: str):
    code = main([*(str(path) for path in paths), *options])
    output = capsys.readouterr()
    return code, output.out, output.err


def test_redirect_line_limit_is_opt_in_and_blocks_unchanged_invalid_policy(tmp_path, capsys):
    paths = _policies(tmp_path, "/a " + "b" * 998 + "\n", "/*\n  X-Robots-Tag: noindex\n")
    code, out, err = _run(capsys, paths, "--json")
    assert code == 0 and err == "" and json.loads(out)["status"] == "pass"

    code, out, err = _run(capsys, paths, "--check-policy-limits", "--json")
    report = json.loads(out)
    assert code == 1 and err == ""
    assert report["status"] == "fail"
    assert report["checks"][0]["reason"] == "limit_exceeded"
    assert "line 1" in report["checks"][0]["limit_issue"]
    assert "1000" in report["checks"][0]["limit_issue"]
    assert report["checks"][0]["expected_sha256"] == report["checks"][0]["observed_sha256"]
    assert report["checks"][1]["status"] == "pass"


def test_header_line_limit_is_clear_in_all_review_formats(tmp_path, capsys):
    paths = _policies(tmp_path, "/old /new 301\n", "/*\n  X-Example: " + "a" * 1990 + "\n")
    reports = tmp_path / "reports"
    reports.mkdir()
    code, out, err = _run(
        capsys, paths, "--check-policy-limits", "--report-dir", str(reports),
        "--github-annotations",
    )
    assert code == 1 and err == "" and out.count("::error") == 1
    assert "_headers" in out and "2000" in out
    report = json.loads((reports / "seo-pages.json").read_text(encoding="utf-8"))
    assert report["checks"][1]["reason"] == "limit_exceeded"
    assert "line 2" in report["checks"][1]["limit_issue"]
    assert "2000" in (reports / "seo-pages.md").read_text(encoding="utf-8")
    failures = ET.parse(reports / "seo-pages.xml").getroot().findall(".//failure")
    assert len(failures) == 1 and "2000" in (failures[0].text or "")


@pytest.mark.parametrize(("redirect_count", "header_count", "failed_artifact"), [
    (2001, 1, "_redirects"),
    (1, 101, "_headers"),
])
def test_reported_rule_count_limits_are_opt_in(
    tmp_path, capsys, redirect_count, header_count, failed_artifact,
):
    redirects = "/a /b 301\n" * redirect_count
    headers = "/*\n  X-Test: ok\n" * header_count
    paths = _policies(tmp_path, redirects, headers)
    code, out, err = _run(capsys, paths, "--check-policy-limits", "--json")
    report = json.loads(out)
    assert code == 1 and err == ""
    failing = [item for item in report["checks"] if item["status"] == "fail"]
    assert len(failing) == 1 and failing[0]["artifact"] == failed_artifact
    assert failing[0]["reason"] == "limit_exceeded"
    assert "rules" in failing[0]["limit_issue"] or "declarations" in failing[0]["limit_issue"]


def test_dynamic_redirect_quota_blocks_matching_bytes(tmp_path, capsys):
    paths = _policies(
        tmp_path, "/x/:slug /new/:slug 301\n" * 101, "/*\n  X-Test: ok\n",
    )
    code, out, err = _run(capsys, paths, "--check-policy-limits", "--json")
    issue = json.loads(out)["checks"][0]["limit_issue"]
    assert code == 1 and err == "" and "101 dynamic" in issue and "100" in issue


def test_limits_boundary_and_offline_behavior(tmp_path, capsys, monkeypatch):
    # These are basic length/count guards, not a parser or routing emulator.
    paths = _policies(
        tmp_path,
        "/a " + "b" * 997 + "\n",
        "/*\n  X-Example: " + "c" * 1987 + "\n",
    )
    monkeypatch.setattr(
        socket, "socket",
        lambda *a, **kw: pytest.fail("policy limits must never fetch URLs"),
    )
    code, out, err = _run(capsys, paths, "--check-policy-limits")
    assert code == 0 and err == "" and "PASS _redirects" in out
    assert "PASS _headers" in out


def test_exact_rule_count_boundaries_and_comments_are_accepted(tmp_path, capsys):
    paths = _policies(
        tmp_path,
        "# reviewed comment\n" + "/a /b 301\n" * 2000
        + "/x/* /new/:splat 301\n" * 100,
        "# reviewed comment\n" + "/*\n  X-Test: ok\n" * 100,
    )
    code, out, err = _run(capsys, paths, "--check-policy-limits", "--json")
    assert code == 0 and err == "" and json.loads(out)["status"] == "pass"


def test_policy_line_limits_count_unicode_characters_not_encoded_bytes(tmp_path, capsys):
    paths = _policies(
        tmp_path, "/a " + "é" * 997 + "\n",
        "/*\n  X-Example: " + "é" * 1987 + "\n",
    )
    code, out, err = _run(capsys, paths, "--check-policy-limits", "--json")
    assert code == 0 and err == "" and json.loads(out)["status"] == "pass"


def test_non_utf8_reviewed_policy_is_explicit_input_error_only_when_opted_in(
    tmp_path, capsys,
):
    paths = _policies(tmp_path, "/a /b 301\n", "/*\n  X-Test: ok\n")
    redirects, _, build = paths
    redirects.write_bytes(b"/a /b 301\n# \xff\n")
    (build / "_redirects").write_bytes(redirects.read_bytes())
    code, out, err = _run(capsys, paths, "--json")
    assert code == 0 and err == "" and json.loads(out)["status"] == "pass"
    code, out, err = _run(capsys, paths, "--check-policy-limits", "--json")
    report = json.loads(out)
    assert code == 2 and err == ""
    assert report["status"] == "error"
    assert report["errors"][0]["artifact"] == "_redirects"
    assert "UTF-8" in report["errors"][0]["error"]
    assert report["checks"][0]["artifact"] == "_headers"
