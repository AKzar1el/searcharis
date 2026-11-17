"""A single offline comparison can supply CI machines and human reviewers."""

import json
import os
import socket
from pathlib import Path
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


def _fail_report_write(monkeypatch, target, *, replace=False):
    original_open = Path.open

    class FailingReport:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            result = self.handle.__exit__(exc_type, exc_value, traceback)
            if replace:
                replacement = target.with_suffix(".replacement")
                replacement.write_text("other writer's report", encoding="utf-8")
                os.replace(replacement, target)
            return result

        def fileno(self):
            return self.handle.fileno()

        def write(self, text):
            self.handle.write(text[:12])
            self.handle.flush()
            raise OSError("simulated comparison report write failure")

    def intercepted_open(self, mode="r", *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if self == target and mode == "x":
            return FailingReport(handle)
        return handle

    monkeypatch.setattr(Path, "open", intercepted_open)


@pytest.mark.parametrize("flag,suffix", [
    ("--json-file", ".json"), ("--sarif-file", ".sarif"),
])
@pytest.mark.parametrize("replaced", [False, True])
def test_failed_comparison_report_write_preserves_only_foreign_replacements(
    tmp_path, monkeypatch, capsys, flag, suffix, replaced,
):
    manifest, baseline, candidate = _fixture(tmp_path)
    report = tmp_path / ("comparison" + suffix)
    _fail_report_write(monkeypatch, report, replace=replaced)

    assert _run(manifest, baseline, candidate, flag, report) == 2
    output = capsys.readouterr()
    assert "simulated comparison report write failure" in output.err
    assert output.out == ""
    if replaced:
        assert report.read_text(encoding="utf-8") == "other writer's report"
        assert "cleanup skipped" in output.err
    else:
        assert not report.exists(), "failed comparison retained its partial artifact"


def test_late_comparison_report_failure_cleans_prior_successful_artifacts(
    tmp_path, monkeypatch, capsys,
):
    manifest, baseline, candidate = _fixture(tmp_path)
    earlier = tmp_path / "comparison.json"
    later = tmp_path / "comparison.sarif"
    _fail_report_write(monkeypatch, later)

    assert _run(manifest, baseline, candidate,
                "--json-file", earlier, "--sarif-file", later) == 2
    assert "simulated comparison report write failure" in capsys.readouterr().err
    assert not earlier.exists()
    assert not later.exists()


@pytest.mark.parametrize("flag,suffix", [
    ("--json-file", ".json"), ("--sarif-file", ".sarif"),
])
def test_comparison_report_cleanup_denial_is_reported(
    tmp_path, monkeypatch, capsys, flag, suffix,
):
    manifest, baseline, candidate = _fixture(tmp_path)
    report = tmp_path / ("comparison" + suffix)
    _fail_report_write(monkeypatch, report)
    original_unlink = Path.unlink

    def deny_unlink(self, *args, **kwargs):
        if self == report:
            raise PermissionError("simulated cleanup refusal")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_unlink)
    assert _run(manifest, baseline, candidate, flag, report) == 2
    output = capsys.readouterr()
    assert "simulated comparison report write failure" in output.err
    assert "partial report cleanup failed" in output.err
    assert report.exists()


def _bundle_paths(directory):
    return [directory / f"seo-comparison.{suffix}" for suffix in (
        "json", "xml", "md", "sarif",
    )]


def test_report_dir_writes_four_comparison_artifacts_in_one_offline_run(
    tmp_path, capsys, monkeypatch,
):
    manifest, baseline, candidate = _fixture(tmp_path)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()

    def no_network(*_args, **_kwargs):
        raise AssertionError("report bundle must not connect to a network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    assert _run(manifest, baseline, candidate, "--report-dir", reports_dir) == 1
    paths = _bundle_paths(reports_dir)
    assert all(path.is_file() for path in paths)
    assert json.loads(paths[0].read_text(encoding="utf-8"))["status"] == "regression"
    assert ElementTree.fromstring(paths[1].read_text(encoding="utf-8")).tag in (
        "testsuite", "testsuites",
    )
    assert "title_presence" in paths[2].read_text(encoding="utf-8")
    assert json.loads(paths[3].read_text(encoding="utf-8"))["version"] == "2.1.0"
    assert capsys.readouterr().out == ""


def test_report_dir_preserves_inherited_debt_and_zero_exit(tmp_path, capsys):
    manifest, baseline, candidate = _fixture(
        tmp_path, baseline_title="", candidate_title="",
    )
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    assert _run(manifest, baseline, candidate, "--report-dir", reports_dir) == 0
    json_path, _, markdown, sarif = _bundle_paths(reports_dir)
    assert json.loads(json_path.read_text(encoding="utf-8"))["status"] != "regression"
    assert "Inherited baseline issues: 1" in markdown.read_text(encoding="utf-8")
    assert json.loads(sarif.read_text(encoding="utf-8"))["runs"][0]["results"] == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("problem", ["existing", "missing", "baseline", "candidate"])
def test_report_dir_preflights_every_target_without_partial_bundle(
    tmp_path, capsys, problem,
):
    manifest, baseline, candidate = _fixture(tmp_path)
    reports_dir = {
        "existing": tmp_path / "reports",
        "missing": tmp_path / "missing",
        "baseline": baseline / "reports",
        "candidate": candidate / "reports",
    }[problem]
    if problem != "missing":
        reports_dir.mkdir()
    paths = _bundle_paths(reports_dir)
    if problem == "existing":
        paths[2].write_text("owner report", encoding="utf-8")

    assert _run(manifest, baseline, candidate, "--report-dir", reports_dir) == 2
    assert "Invalid input" in capsys.readouterr().err
    assert [path.exists() for path in paths] == (
        [False, False, True, False] if problem == "existing" else [False] * 4
    )
    if problem == "existing":
        assert paths[2].read_text(encoding="utf-8") == "owner report"


@pytest.mark.parametrize("conflict", [
    ("--json-file", "explicit.json"),
    ("--junit-file", "explicit.xml"),
    ("--markdown-file", "explicit.md"),
    ("--sarif-file", "explicit.sarif"),
    ("--json", None), ("--junit", None), ("--sarif", None),
])
def test_report_dir_rejects_other_output_modes(tmp_path, conflict):
    manifest, baseline, candidate = _fixture(tmp_path)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    args = ["--report-dir", reports_dir, conflict[0]]
    if conflict[1]:
        args.append(tmp_path / conflict[1])
    with pytest.raises(SystemExit) as failure:
        _run(manifest, baseline, candidate, *args)
    assert failure.value.code == 2
    assert not any(path.exists() for path in _bundle_paths(reports_dir))


def test_report_dir_and_annotations_share_one_safe_comparison(tmp_path, capsys):
    manifest, baseline, candidate = _fixture(tmp_path)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    assert _run(
        manifest, baseline, candidate, "--report-dir", reports_dir,
        "--github-annotations",
    ) == 1
    assert capsys.readouterr().out.count("::error title=SEO new issue::") == 1
    assert all(path.exists() for path in _bundle_paths(reports_dir))


def test_report_dir_failure_emits_no_annotations_and_cleans_owned_files(
    tmp_path, capsys, monkeypatch,
):
    manifest, baseline, candidate = _fixture(tmp_path)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    failing_path = _bundle_paths(reports_dir)[-1]
    _fail_report_write(monkeypatch, failing_path)
    assert _run(
        manifest, baseline, candidate, "--report-dir", reports_dir,
        "--github-annotations",
    ) == 2
    captured = capsys.readouterr()
    assert "simulated comparison report write failure" in captured.err
    assert captured.out == ""
    assert all(not path.exists() for path in _bundle_paths(reports_dir))


def test_report_dir_accepts_reviewed_sarif_source_map(
    tmp_path, monkeypatch, capsys,
):
    manifest, baseline, candidate = _fixture(tmp_path)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    src = tmp_path / "src" / "page.html"
    src.parent.mkdir()
    src.write_text("<h1>reviewed</h1>", encoding="utf-8")
    mapping = tmp_path / "sources.json"
    mapping.write_text(json.dumps({
        "version": 1, "sources": {"index.html": "src/page.html"},
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _run(
        manifest, baseline, candidate, "--report-dir", reports_dir,
        "--sarif-source-map", mapping,
    ) == 1
    sarif = json.loads(_bundle_paths(reports_dir)[-1].read_text(encoding="utf-8"))
    source = sarif["runs"][0]["results"][0]["locations"][0]
    assert source["physicalLocation"]["artifactLocation"]["uri"] == "src/page.html"
    assert capsys.readouterr().out == ""
