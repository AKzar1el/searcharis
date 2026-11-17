"""One-pass standalone SEO CI artifacts with create-only output safety."""

import json
import os
import socket
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from searcharis import site_cli
from searcharis.site_cli import main


def _inputs(tmp_path, *, failing=False):
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text(
        "<html><head><title>Actual</title></head></html>"
        if failing else "<html><head><title>Expected</title></head></html>",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "pages": [{"path": "index.html", "contract": {
            "url": "https://example.test/", "title_equals": "Expected",
        }}],
    }), encoding="utf-8")
    out = tmp_path / "reports"
    out.mkdir()
    return manifest, build, out


def _run(manifest, build, *args):
    return main([str(manifest), str(build), *[str(arg) for arg in args]])


def _bundle_paths(out):
    return [out / "seo-site.json", out / "seo-site.xml", out / "seo-site.sarif"]


@pytest.mark.parametrize("failing", [False, True])
def test_bundle_writes_three_parseable_reports_one_pass(tmp_path, monkeypatch, capsys, failing):
    manifest, build, out = _inputs(tmp_path, failing=failing)

    def deny_network(*_args, **_kwargs):
        raise AssertionError("offline SEO CI attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    assert _run(manifest, build, "--report-dir", out) == (1 if failing else 0)
    assert capsys.readouterr().out == ""
    json_path, junit_path, sarif_path = _bundle_paths(out)
    findings = json.loads(json_path.read_text(encoding="utf-8"))
    suite = ET.fromstring(junit_path.read_bytes())
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert findings["status"] == ("fail" if failing else "pass")
    assert int(suite.attrib["failures"]) == (1 if failing else 0)
    assert sarif["version"] == "2.1.0"
    assert len(sarif["runs"][0]["results"]) == (1 if failing else 0)
    assert [p.name for p in sorted(out.iterdir())] == sorted(p.name for p in _bundle_paths(out))


def test_bundle_annotations_are_only_emitted_after_successful_writes(tmp_path, capsys):
    manifest, build, out = _inputs(tmp_path, failing=True)
    assert _run(manifest, build, "--report-dir", out, "--github-annotations") == 1
    assert "::error" in capsys.readouterr().out


def test_bundle_does_not_repeat_site_verification(tmp_path, monkeypatch):
    manifest, build, out = _inputs(tmp_path)
    original = site_cli.verify_manifest
    invocations = []

    def counting_verify(*args, **kwargs):
        invocations.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(site_cli, "verify_manifest", counting_verify)
    assert _run(manifest, build, "--report-dir", out) == 0
    assert len(invocations) == 1


def test_bundle_on_checked_in_multilingual_build_is_offline(tmp_path, monkeypatch):
    fixture = Path(__file__).resolve().parents[2] / "examples" / "seo-contract"
    out = tmp_path / "reports"
    out.mkdir()

    def deny_network(*_args, **_kwargs):
        raise AssertionError("offline SEO CI attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    assert _run(
        fixture / "bilingual-manifest.json", fixture / "bilingual-build",
        "--require-full-coverage", "--report-dir", out,
    ) == 0
    assert json.loads((out / "seo-site.json").read_text(encoding="utf-8"))["status"] == "pass"
    assert ET.fromstring((out / "seo-site.xml").read_bytes()).attrib["failures"] == "0"


@pytest.mark.parametrize("obstacle", ["existing", "in-build", "missing-dir", "invalid-input"])
def test_bundle_preflight_never_leaves_partial_artifacts(tmp_path, capsys, obstacle):
    manifest, build, out = _inputs(tmp_path, failing=True)
    if obstacle == "existing":
        (out / "seo-site.sarif").write_text("existing job artifact", encoding="utf-8")
    elif obstacle == "in-build":
        out = build
    elif obstacle == "missing-dir":
        out = tmp_path / "absent"
    else:
        manifest.write_text("{malformed", encoding="utf-8")
    assert _run(manifest, build, "--report-dir", out, "--github-annotations") == 2
    output = capsys.readouterr()
    assert output.out == ""
    if obstacle == "existing":
        assert (out / "seo-site.sarif").read_text(encoding="utf-8") == "existing job artifact"
        assert not (out / "seo-site.json").exists()
        assert not (out / "seo-site.xml").exists()
    else:
        assert all(not p.exists() for p in _bundle_paths(out))


@pytest.mark.parametrize("conflict", ["--json", "--sarif", "--json-file", "--sarif-file", "--junit-file"])
def test_bundle_rejects_other_report_modes_before_creating_files(tmp_path, conflict):
    manifest, build, out = _inputs(tmp_path)
    options = ["--report-dir", out, conflict]
    if conflict.endswith("file"):
        options.append(tmp_path / "other.txt")
    with pytest.raises(SystemExit) as error:
        _run(manifest, build, *options)
    assert error.value.code == 2
    assert not list(out.iterdir())


@pytest.mark.parametrize("replace", [False, True])
def test_bundle_later_write_failure_cleans_owned_files_only(
    tmp_path, monkeypatch, capsys, replace,
):
    manifest, build, out = _inputs(tmp_path, failing=True)
    failure = out / "seo-site.sarif"
    original_open = Path.open

    class FailingWrite:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            result = self.handle.__exit__(exc_type, exc_value, traceback)
            if replace:
                replacement = out / "replacement.tmp"
                replacement.write_text("another job's report", encoding="utf-8")
                os.replace(replacement, failure)
            return result

        def fileno(self):
            return self.handle.fileno()

        def write(self, text):
            self.handle.write(text[:10])
            self.handle.flush()
            raise OSError("simulated third-report failure")

    def intercepted_open(self, mode="r", *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if self == failure and mode == "x":
            return FailingWrite(handle)
        return handle

    monkeypatch.setattr(Path, "open", intercepted_open)
    assert _run(manifest, build, "--report-dir", out, "--github-annotations") == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "simulated third-report failure" in output.err
    assert not (out / "seo-site.json").exists()
    assert not (out / "seo-site.xml").exists()
    if replace:
        assert failure.read_text(encoding="utf-8") == "another job's report"
        assert "cleanup skipped" in output.err
    else:
        assert not failure.exists()


def test_bundle_accepts_reviewed_sarif_source_map(tmp_path):
    manifest, build, out = _inputs(tmp_path, failing=True)
    # Source-map input validation and report generation must be compatible.
    mapping = tmp_path / "source-map.json"
    mapping.write_text(json.dumps({"version": 1, "sources": {}}), encoding="utf-8")
    assert _run(manifest, build, "--report-dir", out, "--sarif-source-map", mapping) == 1
    assert json.loads((out / "seo-site.sarif").read_text(encoding="utf-8"))["version"] == "2.1.0"


def test_bundle_serialization_error_is_input_failure_without_artifacts(
    tmp_path, monkeypatch, capsys,
):
    manifest, build, out = _inputs(tmp_path, failing=True)

    def invalid_sarif(*_args, **_kwargs):
        raise ValueError("invalid reviewed SARIF metadata")

    monkeypatch.setattr(site_cli, "build_sarif_report", invalid_sarif)
    assert _run(manifest, build, "--report-dir", out, "--github-annotations") == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "invalid reviewed SARIF metadata" in output.err
    assert not list(out.iterdir())
