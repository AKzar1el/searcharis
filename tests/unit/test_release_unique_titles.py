"""Optional duplicate page-title protection in the offline HTML+sitemap release gate."""

import json
import socket
from unittest.mock import patch

from searcharis.release_cli import main as release_main
from searcharis.site_ci import verify_manifest


def _fixture(tmp_path, *, title_a="Products", title_b="Products", excluded=False,
             other_host=False):
    build = tmp_path / "dist"
    build.mkdir(parents=True)
    pages = []
    for name, title, host in (
        ("a", title_a, "example.test"),
        ("b", title_b, "other.test" if other_host else "example.test"),
    ):
        file = build / f"{name}.html"
        robots = '<meta name="robots" content="noindex">' if excluded and name == "b" else ""
        file.write_text(
            f"<html><head><title>{title}</title>{robots}</head><body></body></html>",
            encoding="utf-8",
        )
        contract = {"url": f"https://{host}/{name}", "title_present": True, "indexable": True}
        if excluded and name == "b":
            contract["indexable"] = False
        pages.append({"path": f"{name}.html", "contract": contract})
    manifest = {"version": 1, "pages": pages}
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    sitemap = tmp_path / "sitemap.xml"
    sitemap.write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://example.test/a</loc></url>'
        + ('' if excluded else '<url><loc>https://other.test/b</loc></url>'
           if other_host else '<url><loc>https://example.test/b</loc></url>')
        + '</urlset>', encoding="utf-8",
    )
    return build, manifest, manifest_file, sitemap


def test_unique_titles_detects_two_reviewed_indexable_pages_with_same_title(tmp_path):
    build, manifest, _, _ = _fixture(tmp_path)
    assert verify_manifest(manifest, build)["status"] == "pass"
    report = verify_manifest(manifest, build, check_unique_titles=True)
    assert report["status"] == "fail"
    assert report["summary"] == {"checked": 2, "passed": 0, "failed": 2}
    assert [p["issues"][0]["code"] for p in report["pages"]] == [
        "title_duplicate", "title_duplicate",
    ]
    assert report["pages"][0]["issues"][0]["actual"]["also_in"] == "b.html"
    assert report["pages"][1]["issues"][0]["actual"]["also_in"] == "a.html"


def test_unique_titles_normalizes_whitespace_but_not_case(tmp_path):
    build, manifest, _, _ = _fixture(tmp_path, title_a="A  Product", title_b=" A Product ")
    assert verify_manifest(manifest, build, check_unique_titles=True)["status"] == "fail"
    (build / "b.html").write_text(
        "<html><head><title>A product</title></head><body></body></html>",
        encoding="utf-8",
    )
    assert verify_manifest(manifest, build, check_unique_titles=True)["status"] == "pass"


def test_unique_titles_skips_reviewed_noindex_and_other_origins(tmp_path):
    build, manifest, _, _ = _fixture(tmp_path, excluded=True)
    assert verify_manifest(manifest, build, check_unique_titles=True)["status"] == "pass"
    build, manifest, _, _ = _fixture(tmp_path / "other", other_host=True)
    assert verify_manifest(manifest, build, check_unique_titles=True)["status"] == "pass"


def test_unique_titles_decodes_entities_and_bounds_reported_title(tmp_path):
    long_title = "A" * 200 + " &amp; launch"
    build, manifest, _, _ = _fixture(
        tmp_path, title_a=long_title, title_b="A" * 200 + " & launch",
    )
    report = verify_manifest(manifest, build, check_unique_titles=True)
    assert report["status"] == "fail"
    values = [page["issues"][0]["actual"] for page in report["pages"]]
    assert len(values[0]["title_prefix"]) == 120
    assert values[0]["title_prefix"] == values[1]["title_prefix"]
    assert values[0]["title_sha256"] == values[1]["title_sha256"]


def test_unique_titles_does_not_replace_optional_title_presence_assertion(tmp_path):
    build, manifest, _, _ = _fixture(tmp_path, title_a=" ", title_b="")
    for page in manifest["pages"]:
        del page["contract"]["title_present"]
    assert verify_manifest(manifest, build, check_unique_titles=True)["status"] == "pass"
    manifest["pages"][0]["contract"]["title_present"] = True
    assert [i["code"] for i in verify_manifest(
        manifest, build, check_unique_titles=True,
    )["pages"][0]["issues"]] == ["title_presence"]


def test_unique_titles_release_cli_blocks_locally_without_network(tmp_path, capsys):
    build, _, manifest_file, sitemap = _fixture(tmp_path)
    args = [str(manifest_file), str(build), str(sitemap)]
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network request")):
        assert release_main([*args, "--json"]) == 0
        capsys.readouterr()
        assert release_main([*args, "--check-unique-titles", "--json"]) == 1
    output = capsys.readouterr()
    assert output.err == ""
    result = json.loads(output.out)
    assert result["status"] == result["site"]["status"] == "fail"
    assert result["sitemap"]["status"] == "pass"
    assert {i["code"] for page in result["site"]["pages"] for i in page["issues"]} == {
        "title_duplicate",
    }


def test_unique_title_failures_appear_in_existing_junit_and_markdown(tmp_path, capsys):
    build, _, manifest_file, sitemap = _fixture(tmp_path)
    args = [str(manifest_file), str(build), str(sitemap), "--check-unique-titles"]
    assert release_main([*args, "--junit"]) == 1
    assert "title_duplicate" in capsys.readouterr().out
    output_path = tmp_path / "summary.md"
    assert release_main([*args, "--markdown-file", str(output_path)]) == 1
    assert "title_duplicate" in output_path.read_text(encoding="utf-8")
