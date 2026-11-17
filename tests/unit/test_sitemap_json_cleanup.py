"""A failed standalone sitemap JSON export must not look like a valid CI artifact."""

import json
import os
from pathlib import Path

import pytest

from searcharis.sitemap_cli import main


def _inputs(tmp_path):
    site = tmp_path / "built"
    site.mkdir()
    sitemap = site / "sitemap.xml"
    sitemap.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://example.test/</loc></url></urlset>',
        encoding="utf-8",
    )
    manifest = tmp_path / "review.json"
    manifest.write_text(json.dumps({
        "version": 1, "pages": [{
            "path": "index.html", "contract": {
                "url": "https://example.test/", "indexable": True,
            },
        }],
    }), encoding="utf-8")
    return manifest, sitemap, tmp_path / "report.json"


def _interrupted_write(monkeypatch, target, *, replaced=False):
    original_open = Path.open

    class Interrupted:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            self.file.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            result = self.file.__exit__(exc_type, exc_value, traceback)
            if replaced:
                replacement = target.with_suffix(".replacement")
                replacement.write_text("other writer's report", encoding="utf-8")
                os.replace(replacement, target)
            return result

        def fileno(self):
            return self.file.fileno()

        def write(self, text):
            self.file.write(text[:13])
            self.file.flush()
            raise OSError("injected partial sitemap JSON write")

    def patched_open(path, mode="r", *args, **kwargs):
        file = original_open(path, mode, *args, **kwargs)
        return Interrupted(file) if path == target and mode == "x" else file

    monkeypatch.setattr(Path, "open", patched_open)


@pytest.mark.parametrize("replaced", [False, True])
def test_sitemap_json_interrupted_write_removes_only_owned_file(
    tmp_path, monkeypatch, capsys, replaced,
):
    manifest, sitemap, report = _inputs(tmp_path)
    _interrupted_write(monkeypatch, report, replaced=replaced)

    assert main([str(manifest), str(sitemap), "--json-file", str(report)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "injected partial sitemap JSON write" in output.err
    if replaced:
        assert report.read_text(encoding="utf-8") == "other writer's report"
        assert "cleanup skipped" in output.err
    else:
        assert not report.exists(), "our truncated artifact must not survive"


def test_sitemap_json_cleanup_denial_is_explicit(tmp_path, monkeypatch, capsys):
    manifest, sitemap, report = _inputs(tmp_path)
    _interrupted_write(monkeypatch, report)
    original_unlink = Path.unlink

    def deny_unlink(path, *args, **kwargs):
        if path == report:
            raise PermissionError("injected cleanup denial")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_unlink)
    assert main([str(manifest), str(sitemap), "--json-file", str(report)]) == 2
    output = capsys.readouterr()
    assert "partial report cleanup failed" in output.err
    assert "injected cleanup denial" in output.err
    assert report.exists()


def test_sitemap_json_success_and_create_only_remain_unchanged(tmp_path, capsys):
    manifest, sitemap, report = _inputs(tmp_path)
    assert main([str(manifest), str(sitemap), "--json-file", str(report)]) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "pass"
    assert capsys.readouterr().out == ""
    original = report.read_bytes()
    assert main([str(manifest), str(sitemap), "--json-file", str(report)]) == 2
    assert report.read_bytes() == original
