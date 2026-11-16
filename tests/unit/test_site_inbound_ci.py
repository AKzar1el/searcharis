"""Explicit inbound navigation assertions for critical reviewed landing pages."""

import json
import socket
from unittest.mock import patch

import pytest

from searcharis.build_compare import main as build_compare_main
from searcharis.release_cli import main as release_main
from searcharis.site_ci import verify_manifest
from searcharis.site_cli import main as site_main


def _site(tmp_path, *, home_links="", services_links="", docs_links=""):
    build = tmp_path / "dist"
    links = {
        "index.html": home_links,
        "services/index.html": services_links,
        "docs/index.html": docs_links,
    }
    for path, markup in links.items():
        artifact = build / path
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            f"<html><head><title>{path}</title></head>"
            f"<body><h1>{path}</h1>{markup}</body></html>",
            encoding="utf-8",
        )
    manifest = {
        "version": 1,
        "pages": [
            {"path": path, "contract": {
                "url": "https://example.test/" if path == "index.html"
                else f"https://example.test/{path.removesuffix('index.html')}",
                "title_present": True, "indexable": True,
            }}
            for path in links
        ],
    }
    return build, manifest


def _page(report, path):
    return next(page for page in report["pages"] if page["path"] == path)


def test_explicit_required_page_without_cross_page_link_fails_but_default_passes(tmp_path):
    build, manifest = _site(
        tmp_path,
        services_links='<a href="/services/">Self is not inbound</a>',
    )
    assert verify_manifest(manifest, build)["status"] == "pass"
    report = verify_manifest(
        manifest, build, required_inbound_paths=("services/index.html",),
    )
    assert report["status"] == "fail"
    assert report["reviewed_inbound"]["missing_paths"] == ["services/index.html"]
    assert _page(report, "services/index.html")["issues"][0]["code"] == (
        "reviewed_inbound_link_missing"
    )
    assert report["summary"]["failed"] == 1


def test_inbound_links_accept_normalized_same_origin_href_and_image_maps(tmp_path):
    build, manifest = _site(
        tmp_path,
        home_links='<a href="https://EXAMPLE.test:443/services/#pricing">Services</a>'
        '<map><area href="/docs/#start" alt="Docs"></map>',
    )
    report = verify_manifest(
        manifest, build,
        required_inbound_paths=("services/index.html", "docs/index.html"),
    )
    assert report["status"] == "pass"
    assert report["reviewed_inbound"]["missing_paths"] == []
    assert report["reviewed_inbound"]["linked_paths"] == [
        "docs/index.html", "services/index.html",
    ]


def test_cross_origin_inert_markup_wrong_query_and_self_links_do_not_count(tmp_path):
    build, manifest = _site(
        tmp_path,
        home_links='<a href="http://example.test/services/">Wrong scheme</a>'
        '<a href="https://elsewhere.test/services/">Other host</a>'
        '<a href="/services/?campaign=external">Distinct query</a>'
        '<template><a href="/services/">Inert</a></template>',
        services_links='<a href="#section">Self</a>',
    )
    report = verify_manifest(
        manifest, build, required_inbound_paths=("services/index.html",),
    )
    assert report["status"] == "fail"
    assert report["reviewed_inbound"]["missing_paths"] == ["services/index.html"]


@pytest.mark.parametrize("paths", [
    ("services/index.html", "services/index.html"),
    ("missing/index.html",),
    ("Services/index.html",),
    ("../services/index.html",),
])
def test_required_paths_fail_closed_on_unknown_unsafe_or_duplicate(tmp_path, paths):
    build, manifest = _site(tmp_path)
    with pytest.raises(ValueError):
        verify_manifest(manifest, build, required_inbound_paths=paths)


def test_release_cli_preserves_sitemap_result_and_never_fetches_urls(tmp_path, capsys):
    build, manifest = _site(tmp_path)
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://example.test/</loc></url>'
        '<url><loc>https://example.test/services/</loc></url>'
        '<url><loc>https://example.test/docs/</loc></url></urlset>',
        encoding="utf-8",
    )
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network attempted")):
        assert release_main([
            str(manifest_file), str(build), str(sitemap),
            "--require-inbound-link", "services/index.html", "--json",
        ]) == 1
        report = json.loads(capsys.readouterr().out)
        json_file = tmp_path / "report.json"
        junit_file = tmp_path / "report.xml"
        markdown_file = tmp_path / "report.md"
        assert release_main([
            str(manifest_file), str(build), str(sitemap),
            "--require-inbound-link", "services/index.html",
            "--json-file", str(json_file),
            "--junit-file", str(junit_file),
            "--markdown-file", str(markdown_file),
        ]) == 1
        capsys.readouterr()
        assert release_main([
            str(manifest_file), str(build), str(sitemap),
            "--require-inbound-link", "not-reviewed/index.html", "--json",
        ]) == 2
        bad_input = json.loads(capsys.readouterr().out)
    assert report["site"]["reviewed_inbound"]["missing_paths"] == ["services/index.html"]
    assert report["sitemap"]["status"] == "pass"
    assert bad_input["status"] == "error"
    assert "absent from manifest" in bad_input["error"]
    assert json.loads(json_file.read_text(encoding="utf-8"))["status"] == "fail"
    assert "reviewed_inbound_link_missing" in junit_file.read_text(encoding="utf-8")
    assert "reviewed_inbound_link_missing" in markdown_file.read_text(encoding="utf-8")


def test_standalone_site_cli_exposes_reviewed_inbound_checks_offline(tmp_path, capsys):
    build, manifest = _site(
        tmp_path, home_links='<a href="/services/">Services</a>',
    )
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network attempted")):
        assert site_main([
            str(manifest_file), str(build), "--require-inbound-link",
            "services/index.html", "--json",
        ]) == 0
        passing = json.loads(capsys.readouterr().out)
        assert passing["reviewed_inbound"]["required_paths"] == ["services/index.html"]
        assert passing["reviewed_inbound"]["linked_paths"] == ["services/index.html"]

        assert site_main([
            str(manifest_file), str(build), "--require-inbound-link",
            "docs/index.html", "--json",
        ]) == 1
        failing = json.loads(capsys.readouterr().out)
        assert failing["reviewed_inbound"]["missing_paths"] == ["docs/index.html"]
        assert "reviewed_inbound_link_missing" in [
            issue["code"] for page in failing["pages"] for issue in page["issues"]
        ]

        assert site_main([
            str(manifest_file), str(build), "--require-inbound-link",
            "not-reviewed.html", "--json",
        ]) == 2
        assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_one_command_build_compare_gates_only_new_inbound_regressions(tmp_path, capsys):
    baseline, manifest = _site(
        tmp_path / "baseline", home_links='<a href="/services/">Services</a>',
    )
    candidate, _ = _site(tmp_path / "candidate")
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    args = [str(manifest_file), str(baseline), str(candidate)]
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network attempted")):
        assert build_compare_main([*args, "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["summary"]["new_issues"] == 0
        assert build_compare_main([
            *args, "--require-inbound-link", "services/index.html", "--json",
        ]) == 1
        delta = json.loads(capsys.readouterr().out)
        assert delta["summary"]["new_issues"] == 1
        assert delta["new_issues"][0]["issue"]["code"] == "reviewed_inbound_link_missing"

        # The same missing link in both trusted snapshots is inherited debt,
        # not a reason to fail a regression-only adoption gate.
        home = baseline / "index.html"
        home.write_text(
            home.read_text(encoding="utf-8").replace(
                '<a href="/services/">Services</a>', "",
            ),
            encoding="utf-8",
        )
        assert build_compare_main([
            *args, "--require-inbound-link", "services/index.html", "--json",
        ]) == 0
        inherited = json.loads(capsys.readouterr().out)
        assert inherited["summary"]["existing_issues"] == 1
        assert inherited["summary"]["new_issues"] == 0

        assert build_compare_main([
            *args, "--require-inbound-link", "not-in-manifest.html", "--json",
        ]) == 2
        assert json.loads(capsys.readouterr().out)["status"] == "error"
