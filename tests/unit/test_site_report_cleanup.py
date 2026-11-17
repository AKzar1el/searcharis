"""Standalone site CI report failures must not leave misleading output files."""

import json
import os
from pathlib import Path

import pytest

from searcharis.site_cli import main


def _site(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / "index.html").write_text(
        "<html><head><title>Actual</title></head><body></body></html>",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "pages": [{
            "path": "index.html",
            "contract": {"url": "https://example.test/", "title_equals": "Expected"},
        }],
    }), encoding="utf-8")
    return build, manifest


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
                foreign = target.with_suffix(".replacement")
                foreign.write_text("other writer's artifact", encoding="utf-8")
                os.replace(foreign, target)
            return result

        def fileno(self):
            return self.handle.fileno()

        def write(self, text):
            self.handle.write(text[:12])
            self.handle.flush()
            raise OSError("simulated report write failure")

    def intercepted_open(self, mode="r", *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if self == target and mode == "x":
            return FailingReport(handle)
        return handle

    monkeypatch.setattr(Path, "open", intercepted_open)


@pytest.mark.parametrize("file_flag,suffix", [
    ("--json-file", ".json"), ("--sarif-file", ".sarif"),
    ("--junit-file", ".xml"),
])
@pytest.mark.parametrize("replaced", [False, True])
def test_failed_report_write_cleans_only_owned_artifact(
    tmp_path, monkeypatch, capsys, file_flag, suffix, replaced,
):
    build, manifest = _site(tmp_path)
    report = tmp_path / ("site" + suffix)
    _fail_report_write(monkeypatch, report, replace=replaced)

    assert main([
        str(manifest), str(build), "--github-annotations", file_flag, str(report),
    ]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "simulated report write failure" in output.err
    if replaced:
        assert report.read_text(encoding="utf-8") == "other writer's artifact"
        assert "cleanup skipped" in output.err
    else:
        assert not report.exists(), "partial owned report was incorrectly retained"


@pytest.mark.parametrize("file_flag,suffix", [
    ("--json-file", ".json"), ("--sarif-file", ".sarif"),
    ("--junit-file", ".xml"),
])
def test_cleanup_denial_is_reported_not_silenced(
    tmp_path, monkeypatch, capsys, file_flag, suffix,
):
    build, manifest = _site(tmp_path)
    report = tmp_path / ("site" + suffix)
    _fail_report_write(monkeypatch, report)
    original_unlink = Path.unlink

    def denied_unlink(self, *args, **kwargs):
        if self == report:
            raise PermissionError("simulated cleanup refusal")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied_unlink)
    assert main([str(manifest), str(build), file_flag, str(report)]) == 2
    output = capsys.readouterr()
    assert "simulated report write failure" in output.err
    assert "partial report cleanup failed" in output.err
    assert report.exists()
