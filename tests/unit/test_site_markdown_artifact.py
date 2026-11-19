"""Standalone reviewed-site Markdown for human CI review, without fetching URLs."""

import importlib
import json
import socket

import pytest

from searcharis.site_cli import main


def _inputs(tmp_path, *, failing=False, extra=False):
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text(
        "<html><head><title>Actual</title></head></html>" if failing
        else "<html><head><title>Expected</title></head></html>",
        encoding="utf-8",
    )
    if extra:
        (build / "extra.html").write_text("<title>Extra</title>", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": 1, "pages": [{
        "path": "index.html", "contract": {
            "url": "https://example.test/", "title_equals": "Expected",
        },
    }]}), encoding="utf-8")
    return manifest, build


def _run(manifest, build, *flags):
    return main([str(manifest), str(build), *[str(x) for x in flags]])


@pytest.mark.parametrize("failing", [False, True])
def test_standalone_markdown_creates_human_ci_report(tmp_path, capsys, failing):
    manifest, build = _inputs(tmp_path, failing=failing)
    target = tmp_path / "site.md"
    assert _run(manifest, build, "--markdown-file", target) == int(failing)
    assert capsys.readouterr().out == ""
    result = target.read_text(encoding="utf-8")
    assert "Searcharis SEO site check" in result
    assert ("Result: FAIL" if failing else "Result: PASS") in result
    assert ("title_equals" in result) is failing
    assert "supplied local" in result


def test_markdown_distinguishes_ungated_coverage_from_blocking(tmp_path):
    manifest, build = _inputs(tmp_path, extra=True)
    normal = tmp_path / "normal.md"
    strict = tmp_path / "strict.md"
    assert _run(manifest, build, "--markdown-file", normal) == 0
    normal_text = normal.read_text(encoding="utf-8")
    assert "1 unlisted (not gated)" in normal_text
    assert "## Unreviewed built HTML (not gated)" in normal_text
    assert "<code>&#x27;extra.html&#x27;</code>" in normal_text
    assert _run(manifest, build, "--require-full-coverage", "--markdown-file", strict) == 1
    text = strict.read_text(encoding="utf-8")
    assert "1 unlisted (blocking)" in text
    assert "Unlisted built HTML" in text


def test_optional_unreviewed_preview_is_bounded_and_escapes_html():
    build_site_markdown = importlib.import_module(
        "searcharis.site_markdown"
    ).build_site_markdown
    paths = [f"page-{i:02d}.html" for i in range(13)]
    paths[0] = "<img src=x onerror=1>.html"
    report = {
        "status": "pass", "summary": {"checked": 1, "passed": 1, "failed": 0},
        "pages": [{"path": "index.html", "issues": []}],
        "coverage": {"discovered": 14, "unlisted": paths,
                     "require_full_coverage": False},
    }
    text = build_site_markdown(report)
    assert "## Unreviewed built HTML (not gated)" in text
    assert "&lt;img src=x onerror=1&gt;" in text
    assert "<img" not in text
    assert "page-09.html" in text
    assert "page-10.html" not in text
    assert "3 more unreviewed" in text
    assert len(text) < 6000


def test_markdown_escapes_untrusted_markup_and_reserves_coverage_slot():
    build_site_markdown = importlib.import_module(
        "searcharis.site_markdown"
    ).build_site_markdown
    report = {
        "status": "fail", "summary": {"checked": 1, "passed": 0, "failed": 1},
        "pages": [{"path": "<img src=x onerror=1>.html", "issues": [
            {"code": "<script>alert(1)</script>\n# forged", "expected": "<bad>",
             "actual": "x"} for _ in range(44)
        ]}],
        "coverage": {"discovered": 2, "unlisted": ["extra.html"],
                     "require_full_coverage": True},
    }
    text = build_site_markdown(report)
    assert text == build_site_markdown(report)
    assert "<script>" not in text and "<img" not in text
    assert "&lt;script&gt;" in text and "&lt;img" in text
    assert "Unlisted built HTML" in text and "extra.html" in text
    assert sum(line.startswith("- Page ") or line.startswith("- Unlisted built HTML:")
               for line in text.splitlines()) == 20
    assert "25 more blocking findings" in text
    assert len(text) < 10000


def test_bundle_includes_markdown_in_same_offline_pass(tmp_path, monkeypatch):
    manifest, build = _inputs(tmp_path, failing=True)
    out = tmp_path / "reports"
    out.mkdir()

    def deny_network(*_args, **_kwargs):
        raise AssertionError("Offline site check attempted network access")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    assert _run(manifest, build, "--report-dir", out) == 1
    assert sorted(p.name for p in out.iterdir()) == sorted([
        "seo-site.json", "seo-site.xml", "seo-site.md", "seo-site.sarif",
    ])
    assert "Result: FAIL" in (out / "seo-site.md").read_text(encoding="utf-8")


def test_bundle_markdown_collision_does_not_write_earlier_reports(tmp_path, capsys):
    manifest, build = _inputs(tmp_path)
    out = tmp_path / "reports"
    out.mkdir()
    (out / "seo-site.md").write_text("someone else's report", encoding="utf-8")
    assert _run(manifest, build, "--report-dir", out, "--github-annotations") == 2
    assert capsys.readouterr().out == ""
    assert sorted(p.name for p in out.iterdir()) == ["seo-site.md"]
    assert (out / "seo-site.md").read_text(encoding="utf-8") == "someone else's report"


def test_markdown_file_rejects_existing_target_and_build_tree(tmp_path):
    manifest, build = _inputs(tmp_path)
    existing = tmp_path / "already.md"
    existing.write_text("another job", encoding="utf-8")
    assert _run(manifest, build, "--markdown-file", existing) == 2
    assert existing.read_text(encoding="utf-8") == "another job"
    assert _run(manifest, build, "--markdown-file", build / "summary.md") == 2
    assert not (build / "summary.md").exists()
