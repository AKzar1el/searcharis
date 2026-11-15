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
