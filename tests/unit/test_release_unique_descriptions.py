"""Opt-in same-origin unique meta descriptions in the offline HTML+sitemap gate."""

import json
import socket
from unittest.mock import patch

from searcharis.release_cli import main as release_main
from searcharis.site_ci import verify_manifest


def _fixture(tmp_path, *, a="Shared description", b="Shared description",
             other_origin=False, noindex_b=False):
    build = tmp_path / "dist"
    build.mkdir(parents=True)
    pages = []
    for name, desc, origin in (
        ("a", a, "https://example.test"),
        ("b", b, "https://other.test" if other_origin else "https://example.test"),
    ):
        meta = f'<meta name="description" content="{desc}">' if desc is not None else ""
        robots = '<meta name="robots" content="noindex">' if noindex_b and name == "b" else ""
        (build / f"{name}.html").write_text(
            f"<html><head><title>{name.upper()}</title>{meta}{robots}</head><body></body></html>",
            encoding="utf-8",
        )
        contract = {
            "url": f"{origin}/{name}", "title_present": True, "indexable": not (
                noindex_b and name == "b"
            ),
        }
        pages.append({"path": f"{name}.html", "contract": contract})
    manifest = {"version": 1, "pages": pages}
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    sitemap = tmp_path / "sitemap.xml"
    urls = ["https://example.test/a"]
    if not noindex_b:
        urls.append(f"https://{'other.test' if other_origin else 'example.test'}/b")
    sitemap.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{url}</loc></url>" for url in urls) + "</urlset>",
        encoding="utf-8",
    )
    return build, manifest, manifest_file, sitemap


def test_duplicate_meta_description_opt_in_fails_both_pages(tmp_path):
    build, manifest, _, _ = _fixture(tmp_path)
    assert verify_manifest(manifest, build)["status"] == "pass"
    report = verify_manifest(manifest, build, check_unique_descriptions=True)
    assert report["status"] == "fail"
    assert report["summary"] == {"checked": 2, "passed": 0, "failed": 2}
    issues = [page["issues"][0] for page in report["pages"]]
    assert [issue["code"] for issue in issues] == [
        "description_duplicate", "description_duplicate",
    ]
    assert [issue["actual"]["also_in"] for issue in issues] == ["b.html", "a.html"]


def test_unique_meta_description_normalizes_whitespace_and_html_entities(tmp_path):
    build, manifest, _, _ = _fixture(
        tmp_path, a="Plan  &amp;  launch", b=" Plan & launch ",
    )
    report = verify_manifest(manifest, build, check_unique_descriptions=True)
    assert report["status"] == "fail"
    observed = [page["issues"][0]["actual"] for page in report["pages"]]
    assert observed[0]["description_sha256"] == observed[1]["description_sha256"]
    (build / "b.html").write_text(
        '<html><head><title>B</title><meta name="description" '
        'content="Plan and launch"></head><body></body></html>',
        encoding="utf-8",
    )
    assert verify_manifest(manifest, build, check_unique_descriptions=True)["status"] == "pass"


def test_unique_meta_description_excludes_noindex_and_other_origins(tmp_path):
    build, manifest, _, _ = _fixture(tmp_path, noindex_b=True)
    assert verify_manifest(manifest, build, check_unique_descriptions=True)["status"] == "pass"
    build, manifest, _, _ = _fixture(tmp_path / "other", other_origin=True)
    assert verify_manifest(manifest, build, check_unique_descriptions=True)["status"] == "pass"


def test_unique_meta_description_blank_is_governed_by_presence_contract(tmp_path):
    build, manifest, _, _ = _fixture(tmp_path, a="", b=None)
    assert verify_manifest(manifest, build, check_unique_descriptions=True)["status"] == "pass"
    manifest["pages"][0]["contract"]["description_present"] = True
    report = verify_manifest(manifest, build, check_unique_descriptions=True)
    assert [issue["code"] for issue in report["pages"][0]["issues"]] == [
        "description_presence",
    ]


def test_unique_meta_description_reports_bounded_prefix_and_stable_digest(tmp_path):
    build, manifest, _, _ = _fixture(
        tmp_path, a="B" * 300 + " &amp; offer", b="B" * 300 + " & offer",
    )
    report = verify_manifest(manifest, build, check_unique_descriptions=True)
    assert report["status"] == "fail"
    observed = [page["issues"][0]["actual"] for page in report["pages"]]
    assert len(observed[0]["description_prefix"]) == 120
    assert observed[0]["description_prefix"] == observed[1]["description_prefix"]
    assert observed[0]["description_sha256"] == observed[1]["description_sha256"]


def test_unique_meta_description_release_reports_and_offline_behavior(tmp_path, capsys):
    build, _, manifest_file, sitemap = _fixture(tmp_path)
    args = [str(manifest_file), str(build), str(sitemap)]
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network request")):
        assert release_main([*args, "--json"]) == 0
        capsys.readouterr()
        assert release_main([*args, "--check-unique-descriptions", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == result["site"]["status"] == "fail"
    assert result["sitemap"]["status"] == "pass"
    assert {issue["code"] for page in result["site"]["pages"]
            for issue in page["issues"]} == {"description_duplicate"}
    assert release_main([*args, "--check-unique-descriptions", "--junit"]) == 1
    assert "description_duplicate" in capsys.readouterr().out
    markdown_file = tmp_path / "summary.md"
    assert release_main([
        *args, "--check-unique-descriptions", "--markdown-file", str(markdown_file),
    ]) == 1
    assert "description_duplicate" in markdown_file.read_text(encoding="utf-8")
