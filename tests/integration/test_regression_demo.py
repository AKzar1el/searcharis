"""Exercise the user-facing, offline baseline/candidate comparison example."""

import json
import socket
import xml.etree.ElementTree as ET
from pathlib import Path

from searcharis.build_compare import main as compare_builds
from searcharis.site_ci import read_manifest, verify_manifest

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "examples" / "seo-contract" / "regression-demo"


def test_runnable_regression_demo_excludes_known_baseline_debt(capsys, monkeypatch):
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
