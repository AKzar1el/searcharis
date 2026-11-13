"""Direct UTF-8 site reports for archived offline CI comparisons."""

import json

import pytest

from searcharis.report_compare import main as compare_main
from searcharis.site_cli import main as site_main


def _build(tmp_path, name: str, *, missing_title: bool = False):
    root = tmp_path / name
    root.mkdir()
    title = "" if missing_title else "<title>Example</title>"
    (root / "index.html").write_text(
        f"<!doctype html><html><head>{title}</head><body></body></html>",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "pages": [{
            "path": "index.html",
            "contract": {"url": "https://example.com/", "title_present": True},
        }],
    }), encoding="utf-8")
    return root, manifest


def test_json_file_writes_identical_utf8_report_without_stdout(tmp_path, capsys):
    root, manifest = _build(tmp_path, "build")
    file_path = tmp_path / "result.json"
    assert site_main([str(manifest), str(root), "--json-file", str(file_path)]) == 0
    output = capsys.readouterr()
    assert output.out == output.err == ""
    raw = file_path.read_bytes()
    assert raw.startswith(b"{\n") and b"\x00" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    artifact = json.loads(raw.decode("utf-8"))
    assert artifact["status"] == "pass"
    assert site_main([str(manifest), str(root), "--json"]) == 0
    assert artifact == json.loads(capsys.readouterr().out)


def test_json_file_preserves_failed_site_report_for_comparison(tmp_path, capsys):
    baseline_root, manifest = _build(tmp_path, "baseline")
    candidate_root, _ = _build(tmp_path, "candidate", missing_title=True)
    before = tmp_path / "baseline.json"
    after = tmp_path / "candidate.json"
    assert site_main([str(manifest), str(baseline_root), "--json-file", str(before)]) == 0
    assert site_main([str(manifest), str(candidate_root), "--json-file", str(after)]) == 1
    assert capsys.readouterr().out == ""
    assert json.loads(after.read_text(encoding="utf-8"))["status"] == "fail"
    assert compare_main([str(before), str(after), "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "regression"


def test_json_file_retains_reviewed_fragment_failures_after_main_sync(tmp_path, capsys):
    root, manifest = _build(tmp_path, "build")
    (root / "index.html").write_text(
        '<!doctype html><html><head><title>Example</title></head>'
        '<body><a href="#missing">Broken section link</a></body></html>',
        encoding="utf-8",
    )
    output_file = tmp_path / "fragment-report.json"
    assert site_main([
        str(manifest), str(root), "--check-reviewed-fragments",
        "--json-file", str(output_file),
    ]) == 1
    assert capsys.readouterr().out == ""
    report = json.loads(output_file.read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["reviewed_fragments"]["missing_targets"] == [
        {"path": "index.html", "target": "https://example.com/#missing"},
    ]


def test_json_file_refuses_to_overwrite_existing_artifact(tmp_path, capsys):
    root, manifest = _build(tmp_path, "build")
    target = tmp_path / "result.json"
    target.write_text("owned", encoding="utf-8")
    assert site_main([str(manifest), str(root), "--json-file", str(target)]) == 2
    assert target.read_text(encoding="utf-8") == "owned"
    assert capsys.readouterr().out == ""


def test_json_file_rejects_destination_inside_immutable_build(tmp_path, capsys):
    root, manifest = _build(tmp_path, "build")
    target = root / "result.json"
    assert site_main([str(manifest), str(root), "--json-file", str(target)]) == 2
    assert not target.exists()
    assert "outside" in capsys.readouterr().err.lower()


def test_json_file_does_not_create_report_for_invalid_manifest(tmp_path, capsys):
    root, manifest = _build(tmp_path, "build")
    manifest.write_text("{invalid", encoding="utf-8")
    target = tmp_path / "invalid.json"
    assert site_main([str(manifest), str(root), "--json-file", str(target)]) == 2
    assert not target.exists()
    assert capsys.readouterr().out == ""


def test_json_file_is_mutually_exclusive_with_stdout_modes(tmp_path):
    root, manifest = _build(tmp_path, "build")
    for option in ("--json", "--sarif"):
        with pytest.raises(SystemExit) as raised:
            site_main([str(manifest), str(root), option, "--json-file", str(tmp_path / "a.json")])
        assert raised.value.code == 2
