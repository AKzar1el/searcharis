"""A failed starter-manifest export must not leave an apparently valid file."""

import os
from pathlib import Path

import pytest

from searcharis.manifest_bootstrap import main


@pytest.mark.parametrize("failure", ["partial", "replaced", "cleanup_denied"])
def test_interrupted_manifest_output_only_cleans_its_own_file(
    tmp_path, monkeypatch, capsys, failure,
):
    build = tmp_path / "dist"
    build.mkdir()
    source = build / "index.html"
    source.write_text("<html><head><title>Example</title></head></html>", encoding="utf-8")
    before = source.read_bytes()
    destination = tmp_path / "manifest.json"
    original_open = Path.open
    original_unlink = Path.unlink

    class InterruptedOutput:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, typ, error, tb):
            result = self.handle.__exit__(typ, error, tb)
            if failure == "replaced":
                foreign = tmp_path / "foreign.json"
                foreign.write_text("other writer", encoding="utf-8")
                os.replace(foreign, destination)
            return result

        def fileno(self):
            return self.handle.fileno()

        def write(self, payload):
            self.handle.write(payload[:12])
            self.handle.flush()
            raise OSError("manifest output interrupted")

    def patched_open(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        return InterruptedOutput(handle) if path == destination and mode == "x" else handle

    def patched_unlink(path, *args, **kwargs):
        if path == destination and failure == "cleanup_denied":
            raise PermissionError("cleanup denied")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", patched_open)
    monkeypatch.setattr(Path, "unlink", patched_unlink)
    assert main([str(build), "https://example.test/", "--output", str(destination)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "manifest output interrupted" in captured.err
    if failure == "partial":
        assert not destination.exists()
        assert "cleanup failed" not in captured.err
    elif failure == "replaced":
        assert destination.read_text(encoding="utf-8") == "other writer"
        assert "cleanup skipped" in captured.err
    else:
        assert destination.exists()
        assert "cleanup failed" in captured.err
        assert "cleanup denied" in captured.err
    assert source.read_bytes() == before
