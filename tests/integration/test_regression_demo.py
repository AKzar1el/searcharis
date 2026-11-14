"""Exercise the user-facing, offline baseline/candidate comparison example."""

import json
import socket
import xml.etree.ElementTree as ET
from pathlib import Path

from searcharis.build_compare import main as compare_builds
from searcharis.site_ci import read_manifest, verify_manifest

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "examples" / "seo-contract" / "regression-demo"


def test_runnable_regression_demo_excludes_known_baseline_debt(
    capsys, monkeypatch, tmp_path
):
    def no_network(*_args, **_kwargs):
        raise AssertionError("Offline regression demo attempted an HTTP connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)

    manifest = DEMO / "manifest.json"
    baseline = DEMO / "baseline"
    candidate = DEMO / "candidate"
    data = read_manifest(manifest)
    old_report = verify_manifest(data, baseline, require_full_coverage=True)
    new_report = verify_manifest(data, candidate, require_full_coverage=True)
    assert old_report["summary"] == {"checked": 2, "passed": 1, "failed": 1}
    assert new_report["summary"] == {"checked": 2, "passed": 0, "failed": 2}
    assert old_report["coverage"]["unlisted"] == []
    assert new_report["coverage"]["unlisted"] == []

    for page in ("index.html", "pricing/index.html"):
        assert not (baseline / page).samefile(candidate / page)

    code = compare_builds([
        str(manifest), str(baseline), str(candidate),
        "--require-full-coverage", "--sarif",
    ])
    output = capsys.readouterr()
    assert code == 1
    assert output.err == ""
    sarif = json.loads(output.out)
    assert sarif["version"] == "2.1.0"
    findings = sarif["runs"][0]["results"]
    assert len(findings) == 1
    assert findings[0]["ruleId"] == "title_presence"
    assert findings[0]["locations"][0]["physicalLocation"]["artifactLocation"][
        "uri"
    ] == "pricing/index.html"

    junit_code = compare_builds([
        str(manifest), str(baseline), str(candidate),
        "--require-full-coverage", "--junit",
    ])
    junit = ET.fromstring(capsys.readouterr().out)
    assert junit_code == 1
    assert junit.attrib["tests"] == "1"
    assert junit.attrib["failures"] == "1"
    assert "pricing/index.html" in junit.find("testcase").attrib["name"]
    assert "title_presence" in junit.find("testcase/failure").attrib["message"]

    report_file = tmp_path / "seo-regressions.xml"
    file_code = compare_builds([
        str(manifest), str(baseline), str(candidate),
        "--require-full-coverage", "--junit-file", str(report_file),
    ])
    output = capsys.readouterr()
    assert file_code == 1
    assert output.out == ""
    assert output.err == ""
    assert b"\x00" not in report_file.read_bytes()
    file_junit = ET.parse(report_file).getroot()
    assert file_junit.attrib["failures"] == "1"
    assert "pricing/index.html" in file_junit.find("testcase").attrib["name"]

    sarif_file = tmp_path / "seo-regressions.sarif"
    sarif_code = compare_builds([
        str(manifest), str(baseline), str(candidate),
        "--require-full-coverage", "--sarif-file", str(sarif_file),
    ])
    output = capsys.readouterr()
    assert sarif_code == 1
    assert output.out == output.err == ""
    sarif_content = sarif_file.read_bytes()
    assert b"\x00" not in sarif_content
    sarif_report = json.loads(sarif_content.decode("utf-8"))
    assert sarif_report["version"] == "2.1.0"
    sarif_results = sarif_report["runs"][0]["results"]
    assert len(sarif_results) == 1
    assert sarif_results[0]["ruleId"] == "title_presence"
    assert sarif_results[0]["locations"][0]["physicalLocation"][
        "artifactLocation"
    ]["uri"] == "pricing/index.html"


def test_runnable_regression_demo_passes_a_repaired_candidate_with_inherited_debt(
    capsys, monkeypatch,
):
    def no_network(*_args, **_kwargs):
        raise AssertionError("Offline regression demo attempted an HTTP connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    baseline = DEMO / "baseline"
    repaired = DEMO / "candidate-passing"
    for path in ("index.html", "pricing/index.html"):
        assert (repaired / path).is_file()
        assert not (baseline / path).samefile(repaired / path)

    code = compare_builds([
        str(DEMO / "manifest.json"), str(baseline), str(repaired),
        "--require-full-coverage", "--json",
    ])
    output = capsys.readouterr()
    assert code == 0
    assert output.err == ""
    report = json.loads(output.out)
    assert report["status"] == "pass"
    assert report["summary"]["new_issues"] == 0
    assert report["summary"]["existing_issues"] == 1
    assert report["summary"]["resolved_issues"] == 0
