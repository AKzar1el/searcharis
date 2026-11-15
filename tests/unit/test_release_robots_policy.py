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
