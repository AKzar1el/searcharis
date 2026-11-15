"""Reviewed offline internal-link nofollow policy and saved-build parity."""

import json
import socket
from copy import deepcopy
from unittest.mock import patch

import pytest

from searcharis.build_compare import main as build_compare_main
from searcharis.release_cli import main as release_main
from searcharis.report_compare import compare_reports
from searcharis.site_ci import verify_manifest
from searcharis.site_cli import main as site_main


def _build(root, home_links, *, head_extra=""):
    for directory, title, links in (
        ("en", "Home", home_links),
        ("about", "About", '<a href="/en/">Home</a>'),
    ):
        page = root / directory / "index.html"
        page.parent.mkdir(parents=True)
        page.write_text(
            f"<html><head><title>{title}</title>"
            f"{head_extra if directory == 'en' else ''}"
            f"</head><body><h1>{title}</h1>{links}</body></html>",
            encoding="utf-8",
        )
    return root


def _manifest():
    return {"version": 1, "pages": [
        {"path": f"{directory}/index.html", "contract": {
            "url": f"https://example.test/{directory}/", "title_present": True,
            "indexable": True,
        }} for directory in ("en", "about")
    ]}


def test_internal_nofollow_is_opt_in_and_deduplicates_same_target(tmp_path):
    build = _build(tmp_path / "dist", (
        '<a href="/about/" rel="NoFollow noopener">About</a>'
        '<a href="/about/#team" rel="ugc,NOFOLLOW">About team</a>'
    ))
    manifest = _manifest()
    assert verify_manifest(manifest, build)["status"] == "pass"
    report = verify_manifest(manifest, build, check_internal_nofollow=True)
    assert report["status"] == "fail"
    assert report["summary"]["failed"] == 1
    assert report["pages"][1]["issues"] == [{
        "code": "internal_link_nofollow",
        "expected": "reviewed same-origin links without rel=nofollow",
        "actual": "https://example.test/about/",
    }]
    assert report["internal_nofollow"]["nofollow_targets"] == [
        {"path": "en/index.html", "target": "https://example.test/about/"},
    ]


def test_internal_nofollow_ignores_external_inert_and_non_nofollow(tmp_path):
    build = _build(tmp_path / "dist", (
        '<a href="https://outside.test/" rel="nofollow">External</a>'
        '<a href="/private/login" rel="nofollow">Unreviewed auth route</a>'
        '<a href="mailto:test@example.test" rel="nofollow">Email</a>'
        '<a href="/about/" rel="nofollowing">Unrelated token</a>'
        '<a href="/about/" rel="sponsored noopener">Sponsored</a>'
        '<template><a href="/about/" rel="nofollow">Inert</a></template>'
        '<textarea><a href="/about/" rel="nofollow">Not a link</a></textarea>'
        '<script>"<a href=/about/ rel=nofollow>"</script>'
        '<area href="/about/" rel="nofollow" alt="No containing map">'
        '<link href="/about/" rel="nofollow">'
    ))
    report = verify_manifest(_manifest(), build, check_internal_nofollow=True)
    assert report["status"] == "pass"
    assert report["internal_nofollow"]["nofollow_targets"] == []


def test_internal_nofollow_uses_first_head_base_and_active_image_map(tmp_path):
    build = _build(tmp_path / "dist", (
        '<a href="../about/" rel="noopener" rel="nofollow">First rel wins</a>'
        '<map name="nav"><area href="../about/" rel="NOFOLLOW" alt="About"></map>'
    ), head_extra='<base href="https://example.test/docs/">')
    report = verify_manifest(_manifest(), build, check_internal_nofollow=True)
    assert report["status"] == "fail"
    assert report["internal_nofollow"]["nofollow_targets"] == [
        {"path": "en/index.html", "target": "https://example.test/about/"},
    ]


def test_internal_nofollow_site_and_release_cli_are_offline(tmp_path, capsys):
    build = _build(tmp_path / "dist", '<a href="/about/" rel="nofollow">About</a>')
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://example.test/en/</loc></url>'
        '<url><loc>https://example.test/about/</loc></url></urlset>', encoding="utf-8",
    )
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network attempted")):
        assert site_main([str(manifest), str(build), "--json"]) == 0
        capsys.readouterr()
        assert site_main([str(manifest), str(build), "--check-internal-nofollow", "--json"]) == 1
        site_report = json.loads(capsys.readouterr().out)
        assert release_main([
            str(manifest), str(build), str(sitemap), "--check-internal-nofollow", "--json",
        ]) == 1
        release_report = json.loads(capsys.readouterr().out)
    assert site_report["internal_nofollow"]["nofollow_targets"]
    assert release_report["site"]["internal_nofollow"]["nofollow_targets"]
    assert release_report["sitemap"]["status"] == "pass"


def test_internal_nofollow_saved_reports_preserve_policy_and_regressions(tmp_path):
    baseline = verify_manifest(
        _manifest(), _build(tmp_path / "old", '<a href="/about/">About</a>'),
        check_internal_nofollow=True,
    )
    candidate = verify_manifest(
        _manifest(), _build(tmp_path / "new", '<a href="/about/" rel="nofollow">About</a>'),
        check_internal_nofollow=True,
    )
    assert baseline["status"] == "pass" and candidate["status"] == "fail"
    assert compare_reports(baseline, candidate)["status"] == "regression"
    assert compare_reports(baseline, candidate)["new_issues"][0]["issue"]["code"] == (
        "internal_link_nofollow"
    )
    without_gate = verify_manifest(_manifest(), tmp_path / "old")
    with pytest.raises(ValueError, match="different check options"):
        compare_reports(baseline, without_gate)
    with pytest.raises(ValueError, match="different check options"):
        compare_reports(without_gate, baseline)


@pytest.mark.parametrize("mutation", ["missing_target", "wrong_target", "wrong_count", "missing_policy"])
def test_internal_nofollow_archived_inconsistent_report_fails_closed(tmp_path, mutation):
    report = verify_manifest(
        _manifest(), _build(tmp_path / "dist", '<a href="/about/" rel="nofollow">About</a>'),
        check_internal_nofollow=True,
    )
    bad = deepcopy(report)
    if mutation == "missing_target":
        bad["internal_nofollow"]["nofollow_targets"] = []
    elif mutation == "wrong_target":
        bad["internal_nofollow"]["nofollow_targets"][0]["target"] = "https://example.test/en/"
    elif mutation == "wrong_count":
        bad["internal_nofollow"]["checked_links"] = 0
    else:
        del bad["internal_nofollow"]
    with pytest.raises(ValueError, match="internal_nofollow"):
        compare_reports(bad, bad)


def test_internal_nofollow_build_comparison_flag_gates_new_issues(tmp_path, capsys):
    before = _build(tmp_path / "before", '<a href="/about/">About</a>')
    after = _build(tmp_path / "after", '<a href="/about/" rel="nofollow">About</a>')
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network attempted")):
        assert build_compare_main([str(manifest), str(before), str(after), "--json"]) == 0
        capsys.readouterr()
        assert build_compare_main([
            str(manifest), str(before), str(after), "--check-internal-nofollow", "--json",
        ]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["new_issues"][0]["issue"]["code"] == "internal_link_nofollow"
