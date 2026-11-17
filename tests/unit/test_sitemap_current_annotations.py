"""Standalone sitemap gate can surface bounded current-build failures in Actions."""

import json

import pytest

import searcharis.sitemap_cli as sitemap_cli
from searcharis.sitemap_cli import main


def _inputs(tmp_path, *, missing=0, excluded=False, alias=False, unreviewed=False):
    pages = [
        {"path": f"page-{i}.html", "contract": {
            "url": f"https://example.test/missing-{i}", "indexable": True,
        }}
        for i in range(missing)
    ]
    present = []
    if excluded:
        pages.append({"path": "excluded.html", "contract": {
            "url": "https://example.test/noindex", "indexable": False,
        }})
        present.append("https://example.test/noindex")
    if alias:
        pages.append({"path": "alias.html", "contract": {
            "url": "https://example.test/alias", "canonical": "https://example.test/",
            "indexable": True,
        }})
        present.append("https://example.test/alias")
    if unreviewed:
        present.append("https://example.test/stale")
    if not pages:
        pages.append({"path": "index.html", "contract": {
            "url": "https://example.test/", "indexable": True,
        }})
        present.append("https://example.test/")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": 1, "pages": pages}), encoding="utf-8")
    site = tmp_path / "dist"
    site.mkdir()
    sitemap = site / "sitemap.xml"
    sitemap.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{url}</loc></url>" for url in present)
        + "</urlset>",
        encoding="utf-8",
    )
    return manifest, sitemap


def test_current_annotations_are_bounded_and_represent_each_blocking_category(
    tmp_path, capsys,
):
    manifest, sitemap = _inputs(
        tmp_path, missing=35, excluded=True, alias=True, unreviewed=True,
    )
    assert main([
        str(manifest), str(sitemap), "--reject-noncanonical-urls",
        "--reject-unreviewed-urls", "--github-annotations",
    ]) == 1
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert captured.err == ""
    assert len(lines) == 20
    assert all(line.startswith("::error title=Sitemap ") for line in lines)
    assert sum("Missing required" in line for line in lines) == 17
    assert any("Noindex URL" in line for line in lines)
    assert any("Noncanonical URL" in line for line in lines)
    assert any("Unreviewed URL" in line for line in lines)


def test_current_annotations_pass_and_invalid_input_emit_no_workflow_errors(
    tmp_path, capsys,
):
    manifest, sitemap = _inputs(tmp_path)
    assert main([str(manifest), str(sitemap), "--github-annotations"]) == 0
    assert capsys.readouterr().out == ""
    assert main([str(manifest), str(tmp_path / "missing.xml"),
                 "--github-annotations"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid input" in captured.err


def test_current_annotations_can_accompany_completed_json_file(tmp_path, capsys):
    manifest, sitemap = _inputs(tmp_path, missing=1)
    target = tmp_path / "report.json"
    assert main([str(manifest), str(sitemap), "--json-file", str(target),
                 "--github-annotations"]) == 1
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "fail"
    assert capsys.readouterr().out.startswith("::error title=Sitemap Missing required::")
    assert main([str(manifest), str(sitemap), "--json-file", str(target),
                 "--github-annotations"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "", "no success annotation before failed artifact write"
    assert "already exists" in captured.err


def test_current_annotations_reject_json_stdout_combination(tmp_path):
    manifest, sitemap = _inputs(tmp_path)
    with pytest.raises(SystemExit, match="2"):
        main([str(manifest), str(sitemap), "--json", "--github-annotations"])


def test_current_annotations_escape_untrusted_urls_and_commands():
    url = "https://example.test/%0A%::fake\r\n::warning\u2028\u2029"
    report = {
        "missing_required": [url], "present_excluded": [],
        "present_noncanonical": [], "unreviewed_urls": [],
    }
    commands = sitemap_cli.format_github_annotations(report)
    assert len(commands) == 1
    assert commands[0].startswith("::error title=Sitemap Missing required::")
    assert "%25" in commands[0]
    assert "::warning" not in commands[0]
    assert "\r" not in commands[0] and "\n" not in commands[0]
    assert "\u2028" not in commands[0] and "\u2029" not in commands[0]
