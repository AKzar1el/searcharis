"""Conservative, opt-in sitewide Pages X-Robots-Tag indexing guard."""

import json
import socket
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from searcharis.pages_policy_cli import main


def _policies(tmp_path: Path, headers_text: str):
    build = tmp_path / "dist"
    build.mkdir()
    redirects = tmp_path / "reviewed-redirects"
    headers = tmp_path / "reviewed-headers"
    redirects.write_text("/old /new 301\n", encoding="utf-8")
    headers.write_text(headers_text, encoding="utf-8")
    (build / "_redirects").write_bytes(redirects.read_bytes())
    (build / "_headers").write_bytes(headers.read_bytes())
    return redirects, headers, build


def _run(capsys, paths, *flags):
    result = main([*(str(p) for p in paths), *flags])
    output = capsys.readouterr()
    return result, output.out, output.err


@pytest.mark.parametrize("directive", [
    "noindex",
    "NONE",
    "NOINDEX, nofollow",
    "googlebot: noindex",
    "Googlebot: NoNe, nofollow",
])
def test_opt_in_guard_blocks_explicit_sitewide_noindex(tmp_path, capsys, directive):
    paths = _policies(tmp_path, f"/*\n  X-Robots-Tag: {directive}\n")
    status, out, err = _run(capsys, paths, "--json")
    assert status == 0 and err == "" and json.loads(out)["status"] == "pass"

    status, out, err = _run(capsys, paths, "--check-sitewide-noindex", "--json")
    report = json.loads(out)
    assert status == 1 and err == "" and report["status"] == "fail"
    assert report["checks"][0]["status"] == "pass"
    check = report["checks"][1]
    assert check["artifact"] == "_headers"
    assert check["status"] == "fail" and check["reason"] == "sitewide_noindex"
    assert "line 2" in check["indexability_issue"]
    assert check["expected_sha256"] == check["observed_sha256"]


@pytest.mark.parametrize("text", [
    "/*\n  X-Robots-Tag: index\n",
    "/private/*\n  X-Robots-Tag: noindex\n",
    "/*\n  X-Robots-Tag: noimageindex\n",
    "/*\n  X-Robots-Tag: nosnippet\n",
    "/*\n  X-Robots-Tag: otherbot: noindex\n",
    "/*\n  X-Robots-Tag: otherbot: nofollow, noindex\n",
    "/*\n  ! X-Robots-Tag\n",
    "/*\n# X-Robots-Tag: noindex\n",
    "/private/*\n  X-Robots-Tag: noindex\n/*\n  X-Robots-Tag: index\n",
])
def test_guard_does_not_flag_narrow_or_nonblocking_rules(tmp_path, capsys, text):
    paths = _policies(tmp_path, text)
    code, out, err = _run(capsys, paths, "--check-sitewide-noindex", "--json")
    assert code == 0 and err == "" and json.loads(out)["status"] == "pass"


def test_broad_guard_emits_actionable_all_report_formats_and_is_offline(
    tmp_path, capsys, monkeypatch,
):
    paths = _policies(
        tmp_path,
        "# comment\n/private/*\n  X-Robots-Tag: noindex\n"
        "/*\n  x-robots-tag: nofollow, GOOGLEBOT: NOINDEX\n",
    )
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(
        socket, "socket",
        lambda *args, **kwargs: pytest.fail("offline Pages guard must not fetch URLs"),
    )
    code, out, err = _run(
        capsys, paths, "--check-sitewide-noindex", "--report-dir", str(reports),
        "--github-annotations",
    )
    assert code == 1 and err == ""
    assert out.count("::error") == 1
    assert "_headers" in out and "noindex" in out.lower()
    report = json.loads((reports / "seo-pages.json").read_text(encoding="utf-8"))
    check = report["checks"][1]
    assert check["reason"] == "sitewide_noindex"
    assert "line 5" in check["indexability_issue"]
    text = (reports / "seo-pages.md").read_text(encoding="utf-8")
    assert "_headers: FAIL" in text and "noindex" in text.lower()
    failures = ET.parse(reports / "seo-pages.xml").getroot().findall(".//failure")
    assert len(failures) == 1
    assert "noindex" in (failures[0].text or "").lower()


def test_human_guard_failure_does_not_claim_policy_drift(tmp_path, capsys):
    paths = _policies(tmp_path, "/*\n  X-Robots-Tag: noindex\n")
    code, out, err = _run(capsys, paths, "--check-sitewide-noindex")
    assert code == 1 and err == ""
    assert "PASS _redirects" in out
    assert "FAIL _headers" in out and "noindex" in out.lower()
    assert "changed" not in out.lower()


@pytest.mark.parametrize("directive", [
    "noindex", "NONE", "googlebot: noindex", "googlebot: nofollow, noindex",
])
def test_opt_in_production_host_rule_blocks_noindex(tmp_path, capsys, directive):
    paths = _policies(
        tmp_path,
        "https://preview.pages.dev/*\n  X-Robots-Tag: noindex\n"
        f"https://EXAMPLE.COM/*\n  X-Robots-Tag: {directive}\n",
    )
    flags = ("--check-sitewide-noindex", "--production-host", "example.com", "--json")
    status, out, err = _run(capsys, paths, "--check-sitewide-noindex", "--json")
    assert status == 0 and err == "" and json.loads(out)["status"] == "pass"

    status, out, err = _run(capsys, paths, *flags)
    report = json.loads(out)
    assert status == 1 and err == "" and report["status"] == "fail"
    issue = report["checks"][1]
    assert issue["reason"] == "sitewide_noindex"
    assert "line 4" in issue["indexability_issue"]
    assert "example.com" in issue["indexability_issue"].lower()
    assert "preview.pages.dev" not in issue["indexability_issue"]


@pytest.mark.parametrize("text", [
    "https://preview.pages.dev/*\n  X-Robots-Tag: noindex\n",
    "https://sub.example.com/*\n  X-Robots-Tag: noindex\n",
    "https://example.com.evil.net/*\n  X-Robots-Tag: noindex\n",
    "https://example.com/private/*\n  X-Robots-Tag: noindex\n",
    "https://example.com/\n  X-Robots-Tag: noindex\n",
    "https://example.com/*\n  X-Robots-Tag: otherbot: noindex\n",
    "https://example.com/*\n  ! X-Robots-Tag\n",
    "# https://example.com/*\n  X-Robots-Tag: noindex\n",
])
def test_production_host_guard_ignores_unrelated_rules(tmp_path, capsys, text):
    paths = _policies(tmp_path, text)
    code, out, err = _run(
        capsys, paths, "--check-sitewide-noindex", "--production-host", "example.com",
        "--json",
    )
    assert code == 0 and err == "" and json.loads(out)["status"] == "pass"


@pytest.mark.parametrize("host", [
    "https://example.com", "example.com/path", "example.com:443",
    "example.com.evil/", "*.example.com", "example..com", "-example.com",
    "example.com\n::error::fake", "example_com", "localhost", "",
])
def test_production_host_rejects_invalid_names(tmp_path, capsys, host):
    paths = _policies(tmp_path, "https://example.com/*\n  X-Robots-Tag: noindex\n")
    with pytest.raises(SystemExit) as error:
        _run(capsys, paths, "--check-sitewide-noindex", "--production-host", host)
    assert error.value.code == 2


def test_production_host_requires_opt_in_guard(tmp_path, capsys):
    paths = _policies(tmp_path, "https://example.com/*\n  X-Robots-Tag: noindex\n")
    with pytest.raises(SystemExit) as error:
        _run(capsys, paths, "--production-host", "example.com")
    assert error.value.code == 2


def test_production_host_works_with_existing_sitewide_rule_and_reports(
    tmp_path, capsys, monkeypatch,
):
    paths = _policies(
        tmp_path,
        "https://preview.pages.dev/*\n  X-Robots-Tag: noindex\n"
        "/*\n  X-Robots-Tag: index\n"
        "https://example.com/*\n  X-Robots-Tag: googlebot: noindex\n",
    )
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(
        socket, "socket", lambda *args, **kwargs: pytest.fail("no network allowed"),
    )
    code, out, err = _run(
        capsys, paths, "--check-sitewide-noindex", "--production-host", "EXAMPLE.COM",
        "--report-dir", str(reports), "--github-annotations",
    )
    assert code == 1 and err == "" and out.count("::error") == 1
    assert "example.com" in out.lower()
    issue = json.loads((reports / "seo-pages.json").read_text(encoding="utf-8"))[
        "checks"
    ][1]
    assert issue["reason"] == "sitewide_noindex"
    assert "line 6" in issue["indexability_issue"]
    assert "example.com" in (reports / "seo-pages.md").read_text(encoding="utf-8").lower()
    failure = ET.parse(reports / "seo-pages.xml").getroot().find(".//failure")
    assert failure is not None and "example.com" in (failure.text or "").lower()
