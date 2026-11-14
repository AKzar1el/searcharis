"""A single offline comparison can supply CI machines and human reviewers."""

import json
import socket
from xml.etree import ElementTree

import pytest

from searcharis.build_compare import main


def _fixture(tmp_path, *, baseline_title="Old", candidate_title=""):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    for root, title in ((baseline, baseline_title), (candidate, candidate_title)):
        (root / "index.html").write_text(
            f"<html><head><title>{title}</title></head></html>", encoding="utf-8",
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "pages": [{"path": "index.html", "contract": {
            "url": "https://seo-ci.invalid/", "title_present": True,
        }}],
    }), encoding="utf-8")
    return manifest, baseline, candidate


def _run(manifest, baseline, candidate, *options):
    return main([str(manifest), str(baseline), str(candidate), *map(str, options)])


def test_combined_comparison_artifacts_show_one_regression_offline(
    tmp_path, capsys, monkeypatch,
):
    manifest, baseline, candidate = _fixture(tmp_path)
    targets = [tmp_path / f"report.{suffix}" for suffix in ("json", "xml", "md", "sarif")]

    def no_network(*_args, **_kwargs):
        raise AssertionError("Offline comparison must never connect")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    assert _run(
        manifest, baseline, candidate,
        "--json-file", targets[0], "--junit-file", targets[1],
        "--markdown-file", targets[2], "--sarif-file", targets[3],
    ) == 1
    assert capsys.readouterr().out == ""
    assert json.loads(targets[0].read_text(encoding="utf-8"))["status"] == "regression"
    assert ElementTree.fromstring(targets[1].read_text(encoding="utf-8")).tag in (
        "testsuite", "testsuites",
    )
    assert "title_presence" in targets[2].read_text(encoding="utf-8")
    sarif = json.loads(targets[3].read_bytes().decode("utf-8"))
    assert sarif["version"] == "2.1.0"
    assert [r["ruleId"] for r in sarif["runs"][0]["results"]] == ["title_presence"]
    assert all(path.read_bytes()[:2] not in (b"\xff\xfe", b"\xfe\xff") for path in targets)


def test_combined_artifacts_preserve_regression_only_exit_and_inherited_debt(
    tmp_path, capsys,
):
    manifest, baseline, candidate = _fixture(
        tmp_path, baseline_title="", candidate_title="",
    )
    markdown, sarif = tmp_path / "known.md", tmp_path / "known.sarif"
    assert _run(
        manifest, baseline, candidate,
        "--markdown-file", markdown, "--sarif-file", sarif,
    ) == 0
    assert "Inherited baseline issues: 1" in markdown.read_text(encoding="utf-8")
    assert json.loads(sarif.read_text(encoding="utf-8"))["runs"][0]["results"] == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("problem", [
    "existing", "same_target", "missing_parent", "candidate_build", "baseline_build",
])
def test_combined_artifacts_preflight_all_targets_without_partial_outputs(
    tmp_path, capsys, problem,
):
    manifest, baseline, candidate = _fixture(tmp_path)
    json_target = tmp_path / "safe.json"
    problematic = {
        "existing": tmp_path / "existing.md",
        "same_target": json_target,
        "missing_parent": tmp_path / "absent" / "summary.md",
        "candidate_build": candidate / "summary.md",
        "baseline_build": baseline / "summary.md",
    }[problem]
    if problem == "existing":
        problematic.write_text("Owner content", encoding="utf-8")
    assert _run(
        manifest, baseline, candidate, "--json-file", json_target,
        "--markdown-file", problematic,
    ) == 2
    assert "Invalid input" in capsys.readouterr().err
    assert not json_target.exists()
    if problem == "existing":
        assert problematic.read_text(encoding="utf-8") == "Owner content"
    elif problem != "same_target":
        assert not problematic.exists()


def test_combined_artifacts_with_reviewed_sarif_source_map(tmp_path, capsys, monkeypatch):
    manifest, baseline, candidate = _fixture(tmp_path)
    source = tmp_path / "src" / "home.html"
    source.parent.mkdir()
    source.write_text("<h1>Reviewed</h1>", encoding="utf-8")
    mapping = tmp_path / "sources.json"
    mapping.write_text(json.dumps({
        "version": 1, "sources": {"index.html": "src/home.html"},
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    sarif, markdown = tmp_path / "report.sarif", tmp_path / "report.md"
    assert _run(
        manifest, baseline, candidate,
        "--sarif-file", sarif, "--markdown-file", markdown, "--sarif-source-map", mapping,
    ) == 1
    location = json.loads(sarif.read_text(encoding="utf-8"))["runs"][0]["results"][0]
    assert location["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/home.html"
    assert markdown.exists()
    assert capsys.readouterr().out == ""
