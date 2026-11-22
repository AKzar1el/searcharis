"""Opt-in reviewed robots.txt policy must participate in every offline release result."""

import json
import socket
import xml.etree.ElementTree as ET

import pytest

from searcharis.release_cli import main


@pytest.fixture
def inputs(tmp_path):
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text(
        '<html><head><title>Home</title></head><body><h1>Home</h1></body></html>',
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": 1, "pages": [
        {"path": "index.html", "contract": {
            "url": "https://example.test/", "title_present": True, "indexable": True,
        }},
    ]}), encoding="utf-8")
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://example.test/</loc></url></urlset>',
        encoding="utf-8",
    )
    policy = tmp_path / "reviewed-robots.txt"
    policy.write_bytes(b"User-agent: *\nAllow: /\n")
    (build / "robots.txt").write_bytes(policy.read_bytes())
    return manifest, build, sitemap, policy


def _run(inputs, *args):
    manifest, build, sitemap, _ = inputs
    return main([str(manifest), str(build), str(sitemap), *map(str, args)])


def test_opt_in_policy_passes_offline_without_changing_default_json(inputs, capsys, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("offline release CI must not open network connections")

    monkeypatch.setattr(socket, "socket", no_network)
    assert _run(inputs, "--json") == 0
    assert set(json.loads(capsys.readouterr().out)) == {"status", "site", "sitemap"}
    assert _run(inputs, "--reviewed-robots", inputs[3], "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert report["robots"]["status"] == "pass"
    assert report["robots"]["artifact"] == "robots.txt"


@pytest.mark.parametrize("policy,blocked", [
    (b"User-agent: *\nDisallow: /\n", True),
    (b"User-agent: Googlebot\nDisallow: /\n", True),
    (b"User-agent: *\nDisallow: /\nAllow: /\n", False),
    (b"User-agent: *\nDisallow: /\nUser-agent: Googlebot\nAllow: /\n", False),
    (b"User-agent: *\nDisallow: /private/\n", False),
    (b"User-agent: *\nDisallow: /*$\n", True),
    (b"User-agent: *\nDisallow: /*\nAllow: /\n", True),
    (b"User-agent: *\nDisallow: /*$\nAllow: /$\n", True),
    (b"User-agent: *\nDisallow: /$\nAllow: /*\n", False),
])
def test_combined_release_opt_in_root_crawl_guard_matches_standalone_semantics(
    inputs, capsys, monkeypatch, policy, blocked,
):
    def no_network(*args, **kwargs):
        raise AssertionError("combined root crawl guard must not open sockets")

    monkeypatch.setattr(socket, "socket", no_network)
    inputs[3].write_bytes(policy)
    (inputs[1] / "robots.txt").write_bytes(policy)

    assert _run(inputs, "--reviewed-robots", inputs[3], "--json") == 0
    assert json.loads(capsys.readouterr().out)["robots"]["status"] == "pass"

    assert _run(
        inputs, "--reviewed-robots", inputs[3], "--check-root-crawl-block", "--json",
    ) == (1 if blocked else 0)
    report = json.loads(capsys.readouterr().out)
    assert report["site"]["status"] == report["sitemap"]["status"] == "pass"
    assert report["status"] == ("fail" if blocked else "pass")
    assert report["robots"]["reason"] == ("root_crawl_block" if blocked else None)
    assert report["robots"]["expected_sha256"] == report["robots"]["observed_sha256"]


def test_combined_root_crawl_guard_requires_reviewed_robots(inputs):
    with pytest.raises(SystemExit) as error:
        _run(inputs, "--check-root-crawl-block")
    assert error.value.code == 2


def test_combined_root_crawl_guard_preserves_drift_priority(inputs, capsys):
    inputs[3].write_bytes(b"User-agent: *\nDisallow: /\n")
    assert _run(
        inputs, "--reviewed-robots", inputs[3], "--check-root-crawl-block", "--json",
    ) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["robots"]["reason"] == "changed"


def test_combined_root_crawl_guard_reports_actionable_distinct_failure(
    inputs, tmp_path, capsys,
):
    policy = b"User-agent: *\nDisallow: /\n"
    inputs[3].write_bytes(policy)
    (inputs[1] / "robots.txt").write_bytes(policy)
    reports = tmp_path / "ci"
    reports.mkdir()
    assert _run(
        inputs, "--reviewed-robots", inputs[3], "--check-root-crawl-block",
        "--report-dir", reports, "--github-annotations",
    ) == 1
    stdout = capsys.readouterr().out
    assert "::error" in stdout and "blocks Googlebot at site root" in stdout
    json_report = json.loads((reports / "seo-release.json").read_text(encoding="utf-8"))
    assert json_report["robots"]["reason"] == "root_crawl_block"
    suite = ET.parse(reports / "seo-release.xml").getroot()
    assert suite.attrib["failures"] == "1"
    assert "robots_policy_root_crawl_block" in suite.find("testcase").attrib["name"]
    assert "Googlebot" in suite.find("testcase/failure").attrib["message"]
    markdown = (reports / "seo-release.md").read_text(encoding="utf-8")
    assert "root_crawl_block" in markdown
    assert "Googlebot" in markdown
    sarif = json.loads((reports / "seo-release.sarif").read_text(encoding="utf-8"))
    assert any(
        result["ruleId"] == "robots_policy_root_crawl_block"
        and "Googlebot" in result["message"]["text"]
        for result in sarif["runs"][0]["results"]
    )


def test_staging_robots_drift_blocks_even_when_html_and_sitemap_pass(inputs, capsys):
    (inputs[1] / "robots.txt").write_bytes(b"User-agent: *\nDisallow: /\n")
    assert _run(inputs, "--reviewed-robots", inputs[3], "--json") == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fail"
    assert report["site"]["status"] == report["sitemap"]["status"] == "pass"
    assert report["robots"]["reason"] == "changed"
    assert report["robots"]["expected_sha256"] != report["robots"]["observed_sha256"]
    assert "Disallow" not in json.dumps(report)


def test_missing_built_robots_is_a_mismatch_not_an_input_error(inputs, capsys):
    (inputs[1] / "robots.txt").unlink()
    assert _run(inputs, "--reviewed-robots", inputs[3], "--json") == 1
    assert json.loads(capsys.readouterr().out)["robots"]["reason"] == "missing"


@pytest.mark.parametrize("bad_source", ["missing", "inside_build", "oversized"])
def test_bad_reviewed_robots_policy_fails_closed_before_report_writes(
    inputs, tmp_path, capsys, bad_source,
):
    policy = inputs[3]
    if bad_source == "missing":
        policy = tmp_path / "absent.txt"
    elif bad_source == "inside_build":
        policy = inputs[1] / "robots.txt"
    else:
        policy.write_bytes(b"x" * 512_001)
    output = tmp_path / "release.json"
    assert _run(inputs, "--reviewed-robots", policy, "--json") == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "error"
    assert _run(inputs, "--reviewed-robots", policy, "--json-file", output) == 2
    assert not output.exists()
    capsys.readouterr()


def test_drift_appears_in_human_and_junit_outputs(inputs, capsys):
    (inputs[1] / "robots.txt").write_bytes(b"User-agent: *\nDisallow: /\n")
    assert _run(inputs, "--reviewed-robots", inputs[3]) == 1
    assert "FAIL robots.txt" in capsys.readouterr().out
    assert _run(inputs, "--reviewed-robots", inputs[3], "--junit") == 1
    suite = ET.fromstring(capsys.readouterr().out)
    assert int(suite.attrib["failures"]) == 1
    assert "robots" in suite.find("testcase").attrib["name"]


def test_drift_is_preserved_in_all_file_report_formats(inputs, tmp_path, capsys):
    (inputs[1] / "robots.txt").write_bytes(b"User-agent: *\nDisallow: /\n")
    names = {kind: tmp_path / f"report.{kind}" for kind in ("json", "junit", "md", "sarif")}
    assert _run(
        inputs, "--reviewed-robots", inputs[3],
        "--json-file", names["json"],
        "--junit-file", names["junit"],
        "--markdown-file", names["md"],
        "--sarif-file", names["sarif"],
    ) == 1
    assert json.loads(names["json"].read_text(encoding="utf-8"))["robots"]["reason"] == "changed"
    assert ET.parse(names["junit"]).getroot().attrib["failures"] == "1"
    markdown = names["md"].read_text(encoding="utf-8")
    assert "Robots" in markdown and "FAIL" in markdown
    sarif = json.loads(names["sarif"].read_text(encoding="utf-8"))
    assert any(r["ruleId"] == "robots_policy_drift" for r in sarif["runs"][0]["results"])
    assert capsys.readouterr().out == ""


def test_required_robots_sitemap_declaration_passes_with_reviewed_policy(
    inputs, capsys, monkeypatch,
):
    def no_network(*args, **kwargs):
        raise AssertionError("sitemap discovery check must remain offline")

    monkeypatch.setattr(socket, "socket", no_network)
    policy = inputs[3]
    policy.write_bytes(
        b"\xef\xbb\xbfUser-agent: *\nAllow: /\n# Sitemap: https://ignored.test/map.xml\n"
        b"sItEmAp: https://example.test/sitemap.xml # reviewed\n"
        b"Sitemap: https://example.test/other.xml\n"
    )
    (inputs[1] / "robots.txt").write_bytes(policy.read_bytes())
    assert _run(inputs, "--reviewed-robots", policy, "--require-robots-sitemap",
                "https://example.test/sitemap.xml", "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["robots"]["status"] == "pass"
    assert report["robots"]["required_sitemap_url"] == "https://example.test/sitemap.xml"


@pytest.mark.parametrize("declaration", [
    b"User-agent: *\nAllow: /\n",
    b"User-agent: *\n# Sitemap: https://example.test/sitemap.xml\n",
    b"User-agent: *\nSitemap: https://example.test/other.xml\n",
    b"User-agent: *\nSitemap: http://example.test/sitemap.xml\n",
    b"User-agent: *\nSitemap: /sitemap.xml\n",
])
def test_missing_or_wrong_reviewed_sitemap_blocks_release(inputs, capsys, declaration):
    policy = inputs[3]
    policy.write_bytes(declaration)
    (inputs[1] / "robots.txt").write_bytes(declaration)
    assert _run(inputs, "--reviewed-robots", policy, "--require-robots-sitemap",
                "https://example.test/sitemap.xml", "--json") == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fail"
    assert report["site"]["status"] == report["sitemap"]["status"] == "pass"
    assert report["robots"]["status"] == "fail"
    assert report["robots"]["reason"] == "sitemap_declaration_missing"
    assert report["robots"]["expected_sha256"] == report["robots"]["observed_sha256"]


def test_sitemap_declaration_failure_surfaces_in_junit_markdown_and_sarif(
    inputs, tmp_path, capsys,
):
    outputs = [tmp_path / "result.xml", tmp_path / "result.md", tmp_path / "result.sarif"]
    assert _run(
        inputs, "--reviewed-robots", inputs[3], "--require-robots-sitemap",
        "https://example.test/sitemap.xml", "--junit-file", outputs[0],
        "--markdown-file", outputs[1], "--sarif-file", outputs[2],
    ) == 1
    suite = ET.parse(outputs[0]).getroot()
    assert suite.attrib["failures"] == "1"
    assert "sitemap" in suite.find("testcase").attrib["name"].lower()
    assert "https://example.test/sitemap.xml" in suite.find("testcase/failure").text
    markdown = outputs[1].read_text(encoding="utf-8")
    assert "FAIL" in markdown and "https://example.test/sitemap.xml" in markdown
    sarif = json.loads(outputs[2].read_text(encoding="utf-8"))
    assert any(
        result["ruleId"] == "robots_sitemap_declaration_missing"
        and "https://example.test/sitemap.xml" in result["message"]["text"]
        for result in sarif["runs"][0]["results"]
    )
    assert capsys.readouterr().out == ""


def test_missing_sitemap_declaration_human_output_names_expected_url(inputs, capsys):
    assert _run(inputs, "--reviewed-robots", inputs[3], "--require-robots-sitemap",
                "https://example.test/sitemap.xml") == 1
    assert "required Sitemap URL 'https://example.test/sitemap.xml'" in capsys.readouterr().out


def test_invalid_reviewed_robots_encoding_is_input_error_when_sitemap_required(inputs, capsys):
    inputs[3].write_bytes(b"User-agent: *\nSitemap: \xff\n")
    (inputs[1] / "robots.txt").write_bytes(inputs[3].read_bytes())
    assert _run(inputs, "--reviewed-robots", inputs[3], "--require-robots-sitemap",
                "https://example.test/sitemap.xml", "--json") == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


@pytest.mark.parametrize("url", [
    "/sitemap.xml", "ftp://example.test/sitemap.xml", "https://user@example.test/map.xml",
    "https://example.test/sitemap.xml#fragment", "https://example.test/a b.xml",
    "https://example.test/" + "a" * 2048,
])
def test_invalid_required_sitemap_is_input_error_without_report(inputs, tmp_path, url, capsys):
    output = tmp_path / "report.json"
    assert _run(inputs, "--reviewed-robots", inputs[3], "--require-robots-sitemap",
                url, "--json-file", output) == 2
    assert not output.exists()
    assert "Invalid input" in capsys.readouterr().err


def test_required_sitemap_option_requires_reviewed_robots(inputs):
    with pytest.raises(SystemExit) as error:
        _run(inputs, "--require-robots-sitemap", "https://example.test/sitemap.xml")
    assert error.value.code == 2


def test_repeated_sitemap_requirements_cannot_silently_discard_first(inputs, capsys):
    policy = inputs[3]
    policy.write_bytes(b"User-agent: *\nSitemap: https://example.test/second.xml\n")
    (inputs[1] / "robots.txt").write_bytes(policy.read_bytes())
    assert _run(
        inputs, "--reviewed-robots", policy,
        "--require-robots-sitemap", "https://example.test/first.xml",
        "--require-robots-sitemap", "https://example.test/second.xml", "--json",
    ) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["site"]["status"] == report["sitemap"]["status"] == "pass"
    assert report["robots"]["reason"] == "sitemap_declaration_missing"
    assert report["robots"]["missing_sitemap_urls"] == ["https://example.test/first.xml"]


def test_repeated_sitemap_requirements_accept_all_and_report_reviewed_set(inputs, capsys):
    policy = inputs[3]
    policy.write_bytes(
        b"User-agent: *\nSitemap: https://example.test/first.xml\n"
        b"sItEmAp: https://example.test/second.xml # note\n"
    )
    (inputs[1] / "robots.txt").write_bytes(policy.read_bytes())
    assert _run(
        inputs, "--reviewed-robots", policy,
        "--require-robots-sitemap", "https://example.test/first.xml",
        "--require-robots-sitemap", "https://example.test/second.xml", "--json",
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert report["robots"]["required_sitemap_urls"] == [
        "https://example.test/first.xml", "https://example.test/second.xml",
    ]
    assert report["robots"]["missing_sitemap_urls"] == []


def test_multiple_missing_sitemaps_are_actionable_in_every_output(inputs, tmp_path, capsys):
    urls = ("https://example.test/a.xml", "https://example.test/b.xml")
    outputs = [tmp_path / "multi.json", tmp_path / "multi.xml",
               tmp_path / "multi.md", tmp_path / "multi.sarif"]
    assert _run(
        inputs, "--reviewed-robots", inputs[3],
        "--require-robots-sitemap", urls[0],
        "--require-robots-sitemap", urls[1],
        "--json-file", outputs[0], "--junit-file", outputs[1],
        "--markdown-file", outputs[2], "--sarif-file", outputs[3],
    ) == 1
    robot = json.loads(outputs[0].read_text(encoding="utf-8"))["robots"]
    assert robot["missing_sitemap_urls"] == list(urls)
    assert all(url in outputs[2].read_text(encoding="utf-8") for url in urls)
    suite = ET.parse(outputs[1]).getroot()
    assert all(url in suite.find("testcase/failure").text for url in urls)
    sarif = json.loads(outputs[3].read_text(encoding="utf-8"))
    assert all(url in sarif["runs"][0]["results"][0]["message"]["text"] for url in urls)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("requirements", [
    ("https://example.test/a.xml", "https://example.test/a.xml"),
    ("https://example.test/a.xml", "/relative.xml"),
    tuple(f"https://example.test/{n}.xml" for n in range(17)),
])
def test_unsafe_repeated_sitemap_requirements_do_not_create_reports(
    inputs, tmp_path, capsys, requirements,
):
    output = tmp_path / "must-not-exist.json"
    args = ["--reviewed-robots", str(inputs[3])]
    for url in requirements:
        args.extend(["--require-robots-sitemap", url])
    assert _run(inputs, *args, "--json-file", output) == 2
    assert not output.exists()
    assert "Invalid input" in capsys.readouterr().err
