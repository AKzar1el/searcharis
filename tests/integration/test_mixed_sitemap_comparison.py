"""Review a sitemap generator's index-to-single-file migration offline."""

import json
import socket
from html import escape

import pytest

from searcharis.sitemap_compare import main

_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
_HOME = "https://example.test/"
_ABOUT = "https://example.test/about"
_DRAFT = "https://example.test/draft"


def _manifest(tmp_path):
    path = tmp_path / "reviewed.json"
    path.write_text(json.dumps({
        "version": 1,
        "pages": [
            {"path": "index.html", "contract": {"url": _HOME, "indexable": True}},
            {"path": "about.html", "contract": {"url": _ABOUT, "indexable": True}},
            {"path": "draft.html", "contract": {"url": _DRAFT, "indexable": False}},
        ],
    }), encoding="utf-8")
    return path


def _snapshot(tmp_path, name, urls, *, indexed):
    directory = tmp_path / name
    directory.mkdir()
    sitemap = directory / ("sitemap-0.xml" if indexed else "sitemap.xml")
    sitemap.write_text(
        f'<urlset xmlns="{_NS}">'
        + "".join(f"<url><loc>{escape(url)}</loc></url>" for url in urls)
        + "</urlset>",
        encoding="utf-8",
    )
    if not indexed:
        return sitemap
    index = directory / "sitemap-index.xml"
    index.write_text(
        f'<sitemapindex xmlns="{_NS}"><sitemap>'
        "<loc>https://example.test/sitemap-0.xml</loc>"
        "</sitemap></sitemapindex>",
        encoding="utf-8",
    )
    return index


@pytest.mark.parametrize("baseline_indexed", [True, False])
def test_mixed_snapshot_migration_identifies_new_missing_page_without_network(
    tmp_path, capsys, monkeypatch, baseline_indexed,
):
    monkeypatch.setattr(
        socket, "socket",
        lambda *args, **kwargs: pytest.fail("Sitemap comparison must be offline"),
    )
    baseline = _snapshot(
        tmp_path, "baseline", [_HOME, _ABOUT], indexed=baseline_indexed,
    )
    candidate = _snapshot(tmp_path, "candidate", [_HOME], indexed=not baseline_indexed)
    option = (
        "--candidate-single-sitemap" if baseline_indexed
        else "--baseline-single-sitemap"
    )
    code = main([
        str(_manifest(tmp_path)), str(baseline), str(candidate),
        option, "--json",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["new"]["missing_required"] == [_ABOUT]


def test_mixed_snapshot_preserves_inherited_debt(tmp_path, capsys):
    baseline = _snapshot(tmp_path, "baseline", [_HOME], indexed=True)
    candidate = _snapshot(tmp_path, "candidate", [_HOME], indexed=False)
    code = main([
        str(_manifest(tmp_path)), str(baseline), str(candidate),
        "--candidate-single-sitemap", "--json",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["inherited"]["missing_required"] == [_ABOUT]


def test_mixed_snapshot_new_excluded_url_blocks_with_optional_review(tmp_path, capsys):
    baseline = _snapshot(tmp_path, "baseline", [_HOME], indexed=False)
    candidate = _snapshot(
        tmp_path, "candidate", [_HOME, _DRAFT, "https://example.test/unreviewed"],
        indexed=True,
    )
    code = main([
        str(_manifest(tmp_path)), str(baseline), str(candidate),
        "--baseline-single-sitemap", "--reject-unreviewed-urls", "--json",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["new"]["present_excluded"] == [_DRAFT]
    assert report["new"]["unreviewed_urls"] == ["https://example.test/unreviewed"]


def test_mixed_snapshot_requires_explicit_format_selection(tmp_path, capsys):
    baseline = _snapshot(tmp_path, "baseline", [_HOME], indexed=True)
    candidate = _snapshot(tmp_path, "candidate", [_HOME], indexed=False)
    code = main([
        str(_manifest(tmp_path)), str(baseline), str(candidate), "--json",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["status"] == "error"


def test_mixed_snapshot_different_site_fails_closed(tmp_path, capsys):
    baseline = _snapshot(tmp_path, "baseline", [_HOME], indexed=True)
    candidate = _snapshot(
        tmp_path, "candidate", ["https://other.test/"], indexed=False,
    )
    code = main([
        str(_manifest(tmp_path)), str(baseline), str(candidate),
        "--candidate-single-sitemap", "--json",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "same site" in report["error"]


def test_mixed_snapshot_indexed_candidate_different_site_fails_closed(tmp_path, capsys):
    baseline = _snapshot(tmp_path, "baseline", [_HOME], indexed=False)
    candidate = _snapshot(tmp_path, "candidate", [_HOME], indexed=True)
    candidate.write_text(
        candidate.read_text(encoding="utf-8").replace(
            "https://example.test/sitemap-0.xml",
            "https://other.test/sitemap-0.xml",
        ), encoding="utf-8",
    )
    code = main([
        str(_manifest(tmp_path)), str(baseline), str(candidate),
        "--baseline-single-sitemap", "--json",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "same site" in report["error"]


def test_mixed_snapshot_empty_candidate_reports_new_missing(tmp_path, capsys):
    baseline = _snapshot(tmp_path, "baseline", [_HOME, _ABOUT], indexed=True)
    candidate = _snapshot(tmp_path, "candidate", [], indexed=False)
    code = main([
        str(_manifest(tmp_path)), str(baseline), str(candidate),
        "--candidate-single-sitemap", "--json",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["new"]["missing_required"] == [_HOME, _ABOUT]


def test_mixed_snapshot_empty_single_baseline_is_invalid(tmp_path, capsys):
    baseline = _snapshot(tmp_path, "baseline", [], indexed=False)
    candidate = _snapshot(tmp_path, "candidate", [_HOME], indexed=True)
    code = main([
        str(_manifest(tmp_path)), str(baseline), str(candidate),
        "--baseline-single-sitemap", "--json",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "nonempty" in report["error"]


def test_mixed_snapshot_report_stays_outside_both_builds(tmp_path, capsys):
    baseline = _snapshot(tmp_path, "baseline", [_HOME], indexed=True)
    candidate = _snapshot(tmp_path, "candidate", [_HOME], indexed=False)
    forbidden = baseline.parent / "result.json"
    code = main([
        str(_manifest(tmp_path)), str(baseline), str(candidate),
        "--candidate-single-sitemap", "--json-file", str(forbidden),
    ])
    assert code == 2
    assert not forbidden.exists()
    assert capsys.readouterr().out == ""


def test_mixed_snapshot_junit_export_fails_only_on_new_regressions(tmp_path, capsys):
    baseline = _snapshot(tmp_path, "baseline", [_HOME, _ABOUT], indexed=True)
    candidate = _snapshot(tmp_path, "candidate", [_HOME], indexed=False)
    artifact = tmp_path / "comparison.xml"
    code = main([
        str(_manifest(tmp_path)), str(baseline), str(candidate),
        "--candidate-single-sitemap", "--junit-file", str(artifact),
    ])
    assert code == 1
    assert artifact.exists()
    assert _ABOUT in artifact.read_text(encoding="utf-8")
    assert capsys.readouterr().out == ""
