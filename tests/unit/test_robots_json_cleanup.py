"""Interrupted offline robots reports must not leave misleading CI artifacts."""

import json
import os
from pathlib import Path

import pytest

from searcharis.robots_policy_cli import main


def _inputs(tmp_path):
    build = tmp_path / "dist"
    build.mkdir()
    reviewed = tmp_path / "reviewed-robots.txt"
    policy = b"User-agent: *\nAllow: /\nSitemap: https://example.test/sitemap.xml\n"
    reviewed.write_bytes(policy)
    (build / "robots.txt").write_bytes(policy)
    return reviewed, build, tmp_path / "robots.json"


def _inject_partial_write(monkeypatch, target, *, replace=False):
    original_open = Path.open

    class Interrupted:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            self.file.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            result = self.file.__exit__(exc_type, exc_value, traceback)
            if replace:
                replacement = target.with_suffix(".replacement")
                replacement.write_text("foreign CI artifact", encoding="utf-8")
                os.replace(replacement, target)
            return result

        def fileno(self):
            return self.file.fileno()

        def write(self, text):
            self.file.write(text[:11])
            self.file.flush()
            raise OSError("injected interrupted robots JSON export")

    def interrupted_open(path, mode="r", *args, **kwargs):
        file = original_open(path, mode, *args, **kwargs)
        return Interrupted(file) if path == target and mode == "x" else file

    monkeypatch.setattr(Path, "open", interrupted_open)


@pytest.mark.parametrize("replace", [False, True])
def test_failed_robots_json_export_only_removes_owned_partial_file(
    tmp_path, capsys, monkeypatch, replace,
):
    reviewed, build, target = _inputs(tmp_path)
    _inject_partial_write(monkeypatch, target, replace=replace)
    assert main([str(reviewed), str(build), "--json-file", str(target)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "injected interrupted robots JSON export" in captured.err
    if replace:
        assert target.read_text(encoding="utf-8") == "foreign CI artifact"
        assert "cleanup skipped" in captured.err
    else:
        assert not target.exists(), "truncated JSON must not masquerade as a report"


def test_robots_json_cleanup_permission_denial_is_reported(
    tmp_path, capsys, monkeypatch,
):
    reviewed, build, target = _inputs(tmp_path)
    _inject_partial_write(monkeypatch, target)
    original_unlink = Path.unlink

    def deny_cleanup(path, *args, **kwargs):
        if path == target:
            raise PermissionError("injected robots report cleanup denial")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_cleanup)
    assert main([str(reviewed), str(build), "--json-file", str(target)]) == 2
    captured = capsys.readouterr()
    assert "partial report cleanup failed" in captured.err
    assert "injected robots report cleanup denial" in captured.err
    assert target.exists()


def test_robots_json_success_and_create_only_are_unchanged(tmp_path, capsys):
    reviewed, build, target = _inputs(tmp_path)
    assert main([str(reviewed), str(build), "--json-file", str(target)]) == 0
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    original_bytes = target.read_bytes()
    assert main([str(reviewed), str(build), "--json-file", str(target)]) == 2
    assert target.read_bytes() == original_bytes
    assert "already exists" in capsys.readouterr().err
