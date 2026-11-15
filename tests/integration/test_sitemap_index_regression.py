"""Regression-only offline sitemap index gates for static-build SEO CI."""

import json
import os
import socket
from html import escape

import pytest

from searcharis.sitemap_compare import main

_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _manifest(tmp_path):
    path = tmp_path / "reviewed.json"
    path.write_text(json.dumps({
        "version": 1,
        "pages": [
            {"path": "index.html", "contract": {
                "url": "https://example.test/", "indexable": True,
            }},
            {"path": "about.html", "contract": {
                "url": "https://example.test/about", "indexable": True,
            }},
            {"path": "draft.html", "contract": {
                "url": "https://example.test/draft", "indexable": False,
            }},
        ],
    }), encoding="utf-8")
    return path


def _snapshot(tmp_path, name, *urls):
    build = tmp_path / name
    build.mkdir()
    shard = build / "sitemap-0.xml"
    shard.write_text(
        f'<urlset xmlns="{_NS}">'
        + "".join(f"<url><loc>{escape(url)}</loc></url>" for url in urls)
        + "</urlset>", encoding="utf-8",
    )
    index = build / "sitemap-index.xml"
    index.write_text(
        f'<sitemapindex xmlns="{_NS}"><sitemap>'
        '<loc>https://example.test/sitemap-0.xml</loc>'
        '</sitemap></sitemapindex>', encoding="utf-8",
    )
    return index


def _run(tmp_path, baseline_urls, candidate_urls, capsys, *options):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", *baseline_urls)
    candidate = _snapshot(tmp_path, "candidate", *candidate_urls)
    code = main([str(manifest), str(baseline), str(candidate), "--json", *options])
    return code, json.loads(capsys.readouterr().out)


def test_inherited_missing_indexable_url_does_not_block(tmp_path, capsys, monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("Offline sitemap comparison must never open a socket")
    monkeypatch.setattr(socket, "socket", deny_network)
    code, report = _run(
        tmp_path, ["https://example.test/"], ["https://example.test/"], capsys,
    )
    assert code == 0
    assert report["status"] == "pass"
    assert report["inherited"]["missing_required"] == ["https://example.test/about"]
    assert report["new"]["missing_required"] == []


def test_new_missing_indexable_url_blocks(tmp_path, capsys):
    code, report = _run(
        tmp_path, ["https://example.test/", "https://example.test/about"],
        ["https://example.test/"], capsys,
    )
    assert code == 1
    assert report["status"] == "regression"
    assert report["new"]["missing_required"] == ["https://example.test/about"]
    assert report["summary"]["new"] == 1


def test_new_noindex_url_blocks_without_inherited_debt(tmp_path, capsys):
    code, report = _run(
        tmp_path, ["https://example.test/"],
        ["https://example.test/", "https://example.test/draft"], capsys,
    )
    assert code == 1
    assert report["new"]["present_excluded"] == ["https://example.test/draft"]
    assert report["inherited"]["missing_required"] == ["https://example.test/about"]


def test_resolved_sitemap_issues_do_not_block(tmp_path, capsys):
    code, report = _run(
        tmp_path, ["https://example.test/", "https://example.test/draft"],
        ["https://example.test/", "https://example.test/about"], capsys,
    )
    assert code == 0
    assert report["summary"]["resolved"] == 2
    assert report["new"]["present_excluded"] == []


def test_unreviewed_sitemap_urls_are_opt_in(tmp_path, capsys):
    extra = "https://example.test/new-page"
    code, report = _run(
        tmp_path, ["https://example.test/"],
        ["https://example.test/", extra], capsys,
    )
    assert code == 0
    assert "unreviewed_urls" not in report["new"]


def test_new_unreviewed_sitemap_url_is_optional_blocker(tmp_path, capsys):
    extra = "https://example.test/new-page"
    code, report = _run(
        tmp_path, ["https://example.test/"],
        ["https://example.test/", extra], capsys, "--reject-unreviewed-urls",
    )
    assert code == 1
    assert report["new"]["unreviewed_urls"] == [extra]


def test_equivalent_unreviewed_url_spellings_are_not_regressions(tmp_path, capsys):
    code, report = _run(
        tmp_path,
        ["https://example.test/", "https://EXAMPLE.test/%7E"],
        ["https://example.test/", "https://example.test/~"],
        capsys, "--reject-unreviewed-urls",
    )
    assert code == 0
    assert report["new"]["unreviewed_urls"] == []
    assert report["inherited"]["unreviewed_urls"] == ["https://example.test/~"]


def test_new_reviewed_noncanonical_url_is_optional_blocker(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["pages"].append({
        "path": "alias.html",
        "contract": {"url": "https://example.test/alias", "indexable": True,
                     "canonical": "https://example.test/"},
    })
    manifest.write_text(json.dumps(data), encoding="utf-8")
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(
        tmp_path, "candidate", "https://example.test/", "https://example.test/alias",
    )
    assert main([str(manifest), str(baseline), str(candidate),
                 "--reject-noncanonical-urls", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["new"]["present_noncanonical"] == ["https://example.test/alias"]


def test_malformed_candidate_fails_closed(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    (candidate.parent / "sitemap-0.xml").write_text("<urlset", encoding="utf-8")
    assert main([str(manifest), str(baseline), str(candidate), "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["status"] == "error"
    assert "sitemap-0.xml" in error["error"]


def test_same_snapshot_cannot_compare_to_itself(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    index = _snapshot(tmp_path, "build", "https://example.test/")
    assert main([str(manifest), str(index), str(index), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_different_public_sites_are_not_comparable(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    candidate.write_text(
        candidate.read_text(encoding="utf-8").replace(
            "https://example.test/sitemap-0.xml", "https://other.test/sitemap-0.xml",
        ), encoding="utf-8",
    )
    assert main([str(manifest), str(baseline), str(candidate), "--json"]) == 2
    assert "same site" in json.loads(capsys.readouterr().out)["error"]


def test_shared_hardlinked_build_artifact_rejected(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    baseline = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/")
    target = candidate.parent / "sitemap-0.xml"
    target.unlink()
    try:
        os.link(baseline.parent / "sitemap-0.xml", target)
    except OSError as error:
        pytest.skip(f"Hardlinks unavailable on test filesystem: {error}")
    assert main([str(manifest), str(baseline), str(candidate), "--json"]) == 2
    assert "hard-linked" in json.loads(capsys.readouterr().out)["error"]


def test_human_output_bounds_url_details(tmp_path, capsys):
    manifest = _manifest(tmp_path)
    base = _snapshot(tmp_path, "baseline", "https://example.test/")
    candidate = _snapshot(tmp_path, "candidate", "https://example.test/draft")
    assert main([str(manifest), str(base), str(candidate)]) == 1
    human = capsys.readouterr().out
    assert "REGRESSION" in human
    assert "https://example.test/draft" in human
