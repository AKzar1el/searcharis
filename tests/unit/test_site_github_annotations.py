"""GitHub Actions annotations for the standalone offline reviewed-site gate."""

import json
import socket

import pytest

from searcharis.site_cli import main


def _build(tmp_path, *, paths=("index.html",), title_expected=None):
    root = tmp_path / "build"
    root.mkdir()
    pages = []
    for path in paths:
        html = root / path
        html.parent.mkdir(parents=True, exist_ok=True)
        html.write_text(
            "<!doctype html><html><head><title>Found</title></head><body></body></html>",
            encoding="utf-8",
        )
        contract = {"url": "https://example.test/" + path, "title_present": True}
        if title_expected is not None:
            contract["title_equals"] = title_expected
        pages.append({"path": path, "contract": contract})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": 1, "pages": pages}), encoding="utf-8")
    return root, manifest


def test_annotations_are_opt_in_and_failure_only(tmp_path, capsys, monkeypatch):
    root, manifest = _build(tmp_path, title_expected="Missing")
    def no_network(*_args, **_kwargs):
        raise AssertionError("Offline CI must not connect to network")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    assert main([str(manifest), str(root)]) == 1
    assert "::error" not in capsys.readouterr().out
    assert main([str(manifest), str(root), "--github-annotations"]) == 1
    out = capsys.readouterr()
    lines = out.out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("::error title=SEO page issue::")
    assert "title_equals" in lines[0] and "index.html" in lines[0]
    assert "file=" not in lines[0] and "line=" not in lines[0]
    assert out.err == ""


def test_annotations_emit_none_when_site_passes(tmp_path, capsys):
    root, manifest = _build(tmp_path)
    assert main([str(manifest), str(root), "--github-annotations"]) == 0
    assert capsys.readouterr().out == ""


def test_unlisted_html_only_blocks_and_annotates_under_strict_coverage(tmp_path, capsys):
    root, manifest = _build(tmp_path)
    (root / "extra.html").write_text("<html><head></head></html>", encoding="utf-8")
    assert main([str(manifest), str(root), "--github-annotations"]) == 0
    assert capsys.readouterr().out == ""
    assert main([
        str(manifest), str(root), "--require-full-coverage", "--github-annotations",
    ]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("::error title=SEO unlisted HTML::")
    assert "extra.html" in lines[0]


def test_annotations_escape_untrusted_workflow_commands_and_are_bounded(tmp_path, capsys):
    hostile = "X%" + chr(13) + chr(10) + "::warning title=spoof::bad"
    root, manifest = _build(tmp_path, title_expected=hostile)
    assert main([str(manifest), str(root), "--github-annotations"]) == 1
    out = capsys.readouterr().out
    assert out.count("::error") == 1
    assert "::warning" not in out
    assert "%25" in out
    assert len(out.splitlines()) == 1

    paths = tuple(f"page-{i:02}/index.html" for i in range(25))
    many = tmp_path / "many"
    many.mkdir()
    root, manifest = _build(many, paths=paths, title_expected="Missing")
    (root / "unlisted.html").write_text("<html></html>", encoding="utf-8")
    assert main([
        str(manifest), str(root), "--github-annotations", "--require-full-coverage",
    ]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 20
    assert all(line.startswith("::error title=SEO ") for line in lines)
    assert any("unlisted HTML" in line for line in lines)
    assert all(len(line) < 500 for line in lines)


def test_annotation_mode_remains_exclusive_and_input_errors_do_not_emit_commands(
    tmp_path, capsys,
):
    root, manifest = _build(tmp_path)
    for option in ("--json", "--sarif"):
        args = [str(manifest), str(root), "--github-annotations", option]
        with pytest.raises(SystemExit) as exc:
            main(args)
        assert exc.value.code == 2
        capsys.readouterr()
    manifest.write_text("{bad", encoding="utf-8")
    assert main([str(manifest), str(root), "--github-annotations"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "Invalid input:" in output.err


@pytest.mark.parametrize("file_option", ("--json-file", "--sarif-file"))
def test_annotations_coexist_with_create_only_report_files(tmp_path, capsys, file_option):
    root, manifest = _build(tmp_path, title_expected="Missing")
    report_path = tmp_path / ("result.json" if file_option == "--json-file" else "result.sarif")
    args = [str(manifest), str(root), "--github-annotations", file_option, str(report_path)]

    assert main(args) == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert len(output.out.splitlines()) == 1
    assert output.out.startswith("::error title=SEO page issue::")
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    if file_option == "--json-file":
        assert saved["status"] == "fail"
    else:
        assert saved["version"] == "2.1.0"


@pytest.mark.parametrize("file_option", ("--json-file", "--sarif-file"))
def test_annotations_with_report_file_silent_on_pass_and_write_errors(
    tmp_path, capsys, file_option,
):
    root, manifest = _build(tmp_path)
    report_path = tmp_path / ("result.json" if file_option == "--json-file" else "result.sarif")
    args = [str(manifest), str(root), "--github-annotations", file_option, str(report_path)]

    assert main(args) == 0
    assert capsys.readouterr().out == ""
    before = report_path.read_bytes()
    assert main(args) == 2
    error = capsys.readouterr()
    assert error.out == ""
    assert "already exists" in error.err
    assert report_path.read_bytes() == before
