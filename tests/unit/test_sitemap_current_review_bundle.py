"""One-pass current-sitemap reviewer reports stay offline and create-only."""

import json
import os
import socket
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from searcharis.comparison_markdown import build_current_sitemap_markdown
from searcharis.sitemap_cli import main


def _inputs(tmp_path, *, failing=False):
    pages = [
        {"path": "index.html", "contract": {
            "url": "https://example.test/", "indexable": True,
        }},
    ]
    urls = ["https://example.test/"]
    if failing:
        pages += [
            {"path": "missing.html", "contract": {
                "url": "https://example.test/missing", "indexable": True,
            }},
            {"path": "excluded.html", "contract": {
                "url": "https://example.test/excluded", "indexable": False,
            }},
            {"path": "alias.html", "contract": {
                "url": "https://example.test/alias",
                "canonical": "https://example.test/", "indexable": True,
            }},
        ]
        urls += [
            "https://example.test/excluded", "https://example.test/alias",
            "https://example.test/unreviewed",
        ]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": 1, "pages": pages}), encoding="utf-8")
    build = tmp_path / "dist"
    build.mkdir()
    sitemap = build / "sitemap.xml"
    sitemap.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{url}</loc></url>" for url in urls)
        + "</urlset>",
        encoding="utf-8",
    )
    return manifest, sitemap, [
        "--reject-noncanonical-urls", "--reject-unreviewed-urls",
    ]


@pytest.mark.parametrize("failing", [False, True])
def test_current_sitemap_bundle_one_pass(tmp_path, capsys, failing):
    manifest, sitemap, flags = _inputs(tmp_path, failing=failing)
    reports = tmp_path / "reports"
    reports.mkdir()
    code = main([str(manifest), str(sitemap), *flags,
                 "--report-dir", str(reports), "--github-annotations"])
    assert code == (1 if failing else 0)
    assert {p.name for p in reports.iterdir()} == {
        "seo-sitemap.json", "seo-sitemap.xml", "seo-sitemap.md",
    }
    data = json.loads((reports / "seo-sitemap.json").read_text(encoding="utf-8"))
    assert data["status"] == ("fail" if failing else "pass")
    suite = ET.parse(reports / "seo-sitemap.xml").getroot()
    assert suite.attrib["failures"] == ("4" if failing else "0")
    markdown = (reports / "seo-sitemap.md").read_text(encoding="utf-8")
    assert markdown.startswith("# Searcharis SEO current sitemap")
    assert "supplied local" in markdown.lower()
    assert ("Missing reviewed indexable URL" in markdown) == failing
    assert "new regression" not in markdown.lower()
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(captured.out.splitlines()) == (4 if failing else 0)


def test_markdown_file_is_standalone_and_does_not_modify_sitemap(tmp_path, capsys):
    manifest, sitemap, _ = _inputs(tmp_path)
    source = sitemap.read_bytes()
    target = tmp_path / "current.md"
    assert main([str(manifest), str(sitemap), "--markdown-file", str(target)]) == 0
    assert "No blocking sitemap findings." in target.read_text(encoding="utf-8")
    assert sitemap.read_bytes() == source
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("failure", ["existing", "missing-directory", "inside-build"])
def test_bundle_preflights_all_destinations_before_writing(tmp_path, capsys, failure):
    manifest, sitemap, _ = _inputs(tmp_path)
    reports = tmp_path / "reports"
    if failure != "missing-directory":
        reports.mkdir()
    if failure == "existing":
        (reports / "seo-sitemap.md").write_text("foreign", encoding="utf-8")
    if failure == "inside-build":
        reports = sitemap.parent
    assert main([str(manifest), str(sitemap), "--report-dir", str(reports),
                 "--github-annotations"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid input" in captured.err
    assert not (reports / "seo-sitemap.json").exists()
    assert not (reports / "seo-sitemap.xml").exists()
    if failure == "existing":
        assert (reports / "seo-sitemap.md").read_text(encoding="utf-8") == "foreign"
    else:
        assert not (reports / "seo-sitemap.md").exists()
    if failure == "missing-directory":
        assert not reports.exists()


@pytest.mark.parametrize("option", ["--json", "--json-file", "--junit-file", "--markdown-file"])
def test_report_dir_rejects_mixed_output_modes(tmp_path, option):
    manifest, sitemap, _ = _inputs(tmp_path)
    reports = tmp_path / "reports"
    reports.mkdir()
    args = [str(manifest), str(sitemap), "--report-dir", str(reports), option]
    if option != "--json":
        args.append(str(tmp_path / "other.report"))
    with pytest.raises(SystemExit, match="2"):
        main(args)
    assert list(reports.iterdir()) == []


@pytest.mark.parametrize("replaced", [False, True])
def test_late_markdown_write_failure_cleans_only_owned_outputs(
    tmp_path, monkeypatch, capsys, replaced,
):
    manifest, sitemap, flags = _inputs(tmp_path, failing=True)
    reports = tmp_path / "reports"
    reports.mkdir()
    markdown = reports / "seo-sitemap.md"
    original_open = Path.open

    class Interrupted:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, typ, error, tb):
            result = self.handle.__exit__(typ, error, tb)
            if replaced:
                foreign = tmp_path / "foreign.md"
                foreign.write_text("other writer", encoding="utf-8")
                os.replace(foreign, markdown)
            return result

        def fileno(self):
            return self.handle.fileno()

        def write(self, text):
            self.handle.write(text[:12])
            self.handle.flush()
            raise OSError("markdown write interrupted")

    def patched_open(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        return Interrupted(handle) if path == markdown and mode == "x" else handle

    monkeypatch.setattr(Path, "open", patched_open)
    assert main([str(manifest), str(sitemap), *flags,
                 "--report-dir", str(reports), "--github-annotations"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "markdown write interrupted" in captured.err
    assert not (reports / "seo-sitemap.json").exists()
    assert not (reports / "seo-sitemap.xml").exists()
    if replaced:
        assert markdown.read_text(encoding="utf-8") == "other writer"
        assert "cleanup skipped" in captured.err
    else:
        assert not markdown.exists()


def test_bundle_never_fetches_declared_urls(tmp_path, monkeypatch):
    manifest, sitemap, flags = _inputs(tmp_path, failing=True)
    reports = tmp_path / "reports"
    reports.mkdir()

    def reject_network(*_args, **_kwargs):
        raise AssertionError("network connection attempted")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    assert main([str(manifest), str(sitemap), *flags,
                 "--report-dir", str(reports)]) == 1
    assert ET.parse(reports / "seo-sitemap.xml").getroot().attrib["failures"] == "4"


def test_markdown_fairly_bounds_and_escapes_hostile_urls():
    report = {
        "status": "fail",
        "summary": {
            "required": 42, "excluded": 1, "unasserted_pages": 3,
            "sitemap_urls": 46,
        },
        "missing_required": [f"https://example.test/{i}" for i in range(40)],
        "present_excluded": ["https://example.test/<script>\nforged-line"],
        "present_noncanonical": ["https://example.test/alias"],
        "unreviewed_urls": ["https://example.test/unreviewed"],
    }
    markdown = build_current_sitemap_markdown(report)
    assert markdown.count("- Missing reviewed indexable URL") == 17
    assert markdown.count("): <code>") == 20
    assert "Reviewed noindex URL unexpectedly present" in markdown
    assert "Reviewed noncanonical URL unexpectedly present" in markdown
    assert "Unreviewed sitemap URL unexpectedly present" in markdown
    assert "&lt;script&gt;" in markdown
    assert "<script>" not in markdown
    assert "\nforged-line" not in markdown
    assert "\\nforged-line" in markdown
    assert "and 23 more current findings" in markdown
    assert "without indexability assertions: 3" in markdown
    assert "baseline" in markdown.lower()
