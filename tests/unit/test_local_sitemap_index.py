"""Astro-style local sitemap indexes must never resolve a URL over the network."""

import gzip
import json
import socket
from html import escape

import pytest

from searcharis.release_cli import main as release_main
from searcharis.sitemap_ci import verify_sitemap_manifest
from searcharis.sitemap_cli import main as sitemap_main

_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _manifest():
    return {"version": 1, "pages": [
        {"path": "index.html", "contract": {
            "url": "https://example.test/", "indexable": True,
            "title_present": True,
        }},
        {"path": "draft.html", "contract": {
            "url": "https://example.test/draft", "indexable": False,
        }},
    ]}


def _index(tmp_path, *locations, xml=None):
    data = xml if xml is not None else (
        f'<sitemapindex xmlns="{_NS}">'
        + "".join(f"<sitemap><loc>{escape(url)}</loc></sitemap>" for url in locations)
        + "</sitemapindex>"
    )
    file = tmp_path / "sitemap-index.xml"
    file.write_text(data, encoding="utf-8")
    return file


def _shard(tmp_path, filename, *locations):
    xml = f'<urlset xmlns="{_NS}">' + "".join(
        f"<url><loc>{escape(url)}</loc></url>" for url in locations
    ) + "</urlset>"
    path = tmp_path / filename
    if filename.endswith(".gz"):
        path.write_bytes(gzip.compress(xml.encode("utf-8"), mtime=0))
    else:
        path.write_text(xml, encoding="utf-8")
    return path


def test_astro_index_discovers_local_xml_and_gzip_without_network(tmp_path, monkeypatch):
    _shard(tmp_path, "sitemap-0.xml", "https://example.test/")
    _shard(tmp_path, "sitemap-1.xml.gz", "https://example.test/about")
    index = _index(
        tmp_path, "https://example.test/sitemap-0.xml",
        "https://example.test/sitemap-1.xml.gz",
    )
    def no_network(*args, **kwargs):
        raise AssertionError("Sitemap index must not open sockets")
    monkeypatch.setattr(socket, "socket", no_network)
    report = verify_sitemap_manifest(_manifest(), [], sitemap_index_path=index)
    assert report["status"] == "pass"
    assert report["summary"]["sitemap_urls"] == 2


def test_index_discovers_noindex_regression_in_second_shard(tmp_path):
    _shard(tmp_path, "sitemap-0.xml", "https://example.test/")
    _shard(tmp_path, "sitemap-1.xml", "https://example.test/draft")
    index = _index(tmp_path, "https://example.test/sitemap-0.xml",
                   "https://example.test/sitemap-1.xml")
    report = verify_sitemap_manifest(_manifest(), [], sitemap_index_path=index)
    assert report["status"] == "fail"
    assert report["present_excluded"] == ["https://example.test/draft"]


@pytest.mark.parametrize("indexed_url", [
    "https://wrong.test/sitemap-0.xml",
    "http://example.test/sitemap-0.xml",
])
def test_index_url_site_must_match_shard_pages(tmp_path, indexed_url):
    _shard(tmp_path, "sitemap-0.xml", "https://example.test/")
    index = _index(tmp_path, indexed_url)
    with pytest.raises(ValueError, match="site"):
        verify_sitemap_manifest(_manifest(), [], sitemap_index_path=index)


def test_index_rejects_empty_query_delimiter_on_shard_url(tmp_path):
    _shard(tmp_path, "sitemap-0.xml", "https://example.test/")
    index = _index(tmp_path, "https://example.test/sitemap-0.xml?")
    with pytest.raises(ValueError, match="query or fragment"):
        verify_sitemap_manifest(_manifest(), [], sitemap_index_path=index)


def test_index_empty_query_fails_cli_before_creating_json_report(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    _shard(tmp_path, "sitemap-0.xml", "https://example.test/")
    index = _index(tmp_path, "https://example.test/sitemap-0.xml?")
    report_path = tmp_path / "result.json"
    code = sitemap_main([
        str(manifest), "--sitemap-index", str(index),
        "--json-file", str(report_path),
    ])
    assert code == 2
    assert not report_path.exists()
    assert "query or fragment" in capsys.readouterr().err


def test_index_cli_json_file_cannot_modify_index_build_dir(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    build = tmp_path / "dist"
    build.mkdir()
    _shard(build, "sitemap-0.xml", "https://example.test/")
    index = _index(build, "https://example.test/sitemap-0.xml")
    report = build / "report.json"

    assert sitemap_main([
        str(manifest), "--sitemap-index", str(index), "--json-file", str(report),
    ]) == 2
    assert not report.exists()
    assert "outside the supplied sitemap directory" in capsys.readouterr().err


@pytest.mark.parametrize("locations,expected", [
    ([], "1 to"),
    (["https://other.test/sitemap-0.xml", "https://example.test/sitemap-1.xml"], "same"),
    (["https://example.test/x/sitemap-0.xml", "https://example.test/y/sitemap-1.xml"], "same"),
    (["https://example.test/%2e%2e/sitemap-0.xml"], "percent"),
    (["https://example.test/sitemap-0.xml?x=1"], "query"),
    (["https://example.test/../../secret.xml"], "same-directory"),
    (["https://example.test/sitemap-0.xml", "https://example.test/sitemap-0.xml"], "Duplicate"),
    (["https://example.test/sitemap-0.xml", "https://example.test/SITEMAP-0.XML"], "Duplicate"),
])
def test_index_invalid_loc_fails_closed(tmp_path, locations, expected):
    index = _index(tmp_path, *locations)
    with pytest.raises(ValueError, match=expected):
        verify_sitemap_manifest(_manifest(), [], sitemap_index_path=index)


def test_index_rejects_wrong_root_dtd_and_excess_shards(tmp_path):
    for xml in (
        f'<urlset xmlns="{_NS}"/>',
        f'<!DOCTYPE sitemapindex [<!ENTITY x "bad">]><sitemapindex xmlns="{_NS}"/>',
    ):
        index = _index(tmp_path, xml=xml)
        with pytest.raises(ValueError):
            verify_sitemap_manifest(_manifest(), [], sitemap_index_path=index)
    index = _index(tmp_path, *(f"https://example.test/sitemap-{i}.xml" for i in range(17)))
    with pytest.raises(ValueError, match="16"):
        verify_sitemap_manifest(_manifest(), [], sitemap_index_path=index)


def test_index_fails_on_missing_or_case_mismatched_local_file(tmp_path):
    index = _index(tmp_path, "https://example.test/sitemap-0.xml")
    with pytest.raises((OSError, ValueError)):
        verify_sitemap_manifest(_manifest(), [], sitemap_index_path=index)
    _shard(tmp_path, "Sitemap-0.xml", "https://example.test/")
    with pytest.raises((OSError, ValueError)):
        verify_sitemap_manifest(_manifest(), [], sitemap_index_path=index)


def test_index_input_path_must_match_actual_filename_case(tmp_path):
    _shard(tmp_path, "sitemap-0.xml", "https://example.test/")
    actual_index = _index(tmp_path, "https://example.test/sitemap-0.xml")
    actual_index.rename(tmp_path / "Sitemap-Index.xml")

    # An NTFS lookup may resolve this misspelled path, whereas Linux CI and
    # case-sensitive static hosts will not serve the same artifact name.
    with pytest.raises((OSError, ValueError)):
        verify_sitemap_manifest(
            _manifest(), [], sitemap_index_path=tmp_path / "sitemap-index.xml",
        )


def test_index_cli_wrong_case_input_never_writes_json_report(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    build = tmp_path / "dist"
    build.mkdir()
    _shard(build, "sitemap-0.xml", "https://example.test/")
    _index(build, "https://example.test/sitemap-0.xml").rename(
        build / "Sitemap-Index.xml"
    )
    report_dir = tmp_path / "ci-reports"
    report_dir.mkdir()
    report = report_dir / "report.json"
    assert sitemap_main([
        str(manifest), "--sitemap-index", str(build / "sitemap-index.xml"),
        "--json-file", str(report),
    ]) == 2
    assert not report.exists()
    assert "Invalid input:" in capsys.readouterr().err


def test_index_rejects_ambiguous_explicit_shards(tmp_path):
    shard = _shard(tmp_path, "sitemap-0.xml", "https://example.test/")
    index = _index(tmp_path, "https://example.test/sitemap-0.xml")
    with pytest.raises(ValueError, match="either"):
        verify_sitemap_manifest(_manifest(), [shard], sitemap_index_path=index)


def test_standalone_cli_uses_index_and_reports_failure(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    _shard(tmp_path, "sitemap-0.xml", "https://example.test/draft")
    index = _index(tmp_path, "https://example.test/sitemap-0.xml")
    code = sitemap_main([str(manifest), "--sitemap-index", str(index), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["missing_required"] == ["https://example.test/"]


def test_release_cli_index_without_positional_shards(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text('<html><head><title>Home</title></head></html>',
                                      encoding="utf-8")
    (build / "draft.html").write_text(
        '<html><head><meta name="robots" content="noindex"></head></html>',
        encoding="utf-8",
    )
    _shard(build, "sitemap-0.xml", "https://example.test/")
    index = _index(build, "https://example.test/sitemap-0.xml")
    code = release_main([str(manifest), str(build), "--sitemap-index", str(index), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["status"] == "pass"
    assert report["sitemap"]["status"] == "pass"
    assert report["sitemap"]["summary"]["sitemap_urls"] == 1


def test_legacy_explicit_shard_behavior_unchanged(tmp_path):
    shard = _shard(tmp_path, "sitemap-0.xml", "https://example.test/")
    report = verify_sitemap_manifest(_manifest(), [shard])
    assert report["status"] == "pass"
