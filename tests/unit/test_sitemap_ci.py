"""Offline sitemap intent checks: explicit policy, no network or hosting inference."""

import json
from html import escape

import pytest

from searcharis.sitemap_ci import verify_sitemap_manifest
from searcharis.sitemap_cli import main


def _manifest(*contracts):
    return {
        "version": 1,
        "pages": [
            {"path": f"page-{i}.html", "contract": contract}
            for i, contract in enumerate(contracts)
        ],
    }


def _sitemap(tmp_path, *urls, xml=None):
    payload = xml if xml is not None else (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{escape(url)}</loc></url>" for url in urls)
        + "</urlset>"
    )
    path = tmp_path / "sitemap.xml"
    path.write_text(payload, encoding="utf-8")
    return path


def test_pass_only_for_explicit_required_and_excluded_urls(tmp_path):
    manifest = _manifest(
        {"url": "https://example.test/", "indexable": True},
        {"url": "https://example.test/404", "indexable": False},
        {"url": "https://example.test/draft"},
    )
    report = verify_sitemap_manifest(manifest, _sitemap(tmp_path, "https://example.test/"))
    assert report["status"] == "pass"
    assert report["summary"] == {
        "required": 1, "excluded": 1, "unasserted_pages": 1, "sitemap_urls": 1,
    }
    assert report["missing_required"] == []
    assert report["present_excluded"] == []


def test_missing_explicit_indexable_page_fails(tmp_path):
    manifest = _manifest({"url": "https://example.test/about", "indexable": True})
    report = verify_sitemap_manifest(manifest, _sitemap(tmp_path, "https://example.test/"))
    assert report["status"] == "fail"
    assert report["missing_required"] == ["https://example.test/about"]


def test_explicit_noindex_page_in_sitemap_fails(tmp_path):
    manifest = _manifest({"url": "https://example.test/noindex", "indexable": False})
    report = verify_sitemap_manifest(manifest, _sitemap(tmp_path, "https://example.test/noindex"))
    assert report["status"] == "fail"
    assert report["present_excluded"] == ["https://example.test/noindex"]


def test_reviewed_canonical_target_is_required_instead_of_noncanonical_url(tmp_path):
    manifest = _manifest({
        "url": "https://example.test/alias", "canonical": "https://example.test/canonical",
        "indexable": True,
    })
    report = verify_sitemap_manifest(manifest, _sitemap(tmp_path, "https://example.test/canonical"))
    assert report["status"] == "pass"
    assert report["summary"]["required"] == 1


def test_alias_sitemap_url_matches_existing_http_identity(tmp_path):
    manifest = _manifest({"url": "https://EXAMPLE.test/%7Eme", "indexable": True})
    report = verify_sitemap_manifest(manifest, _sitemap(tmp_path, "https://example.test:443/~me"))
    assert report["status"] == "pass"


def test_unknown_sitemap_pages_not_reported_as_failures(tmp_path):
    manifest = _manifest({"url": "https://example.test/", "indexable": True})
    report = verify_sitemap_manifest(
        manifest, _sitemap(tmp_path, "https://example.test/", "https://example.test/other"),
    )
    assert report["status"] == "pass"
    assert report["summary"]["sitemap_urls"] == 2


def test_no_explicit_indexability_policy_is_not_a_false_pass(tmp_path):
    with pytest.raises(ValueError, match="explicit indexable"):
        verify_sitemap_manifest(
            _manifest({"url": "https://example.test/"}),
            _sitemap(tmp_path, "https://example.test/"),
        )


@pytest.mark.parametrize("xml", [
    "<urlset>",
    "<urlset><url><loc>https://example.test/</loc></url></urlset>",
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url/></urlset>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url>'
    '<loc>https://example.test/</loc><loc>https://example.test/2</loc></url></urlset>',
    '<!DOCTYPE urlset [<!ENTITY test "https://example.test/">]>'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    '<url><loc>&test;</loc></url></urlset>',
])
def test_invalid_xml_is_input_error(tmp_path, xml):
    with pytest.raises(ValueError):
        verify_sitemap_manifest(_manifest({"url": "https://example.test/", "indexable": True}), _sitemap(tmp_path, xml=xml))


@pytest.mark.parametrize("url", [
    "/relative", "javascript:alert(1)", "https://example.test/path#fragment",
    "https://user:pass@example.test/", "https://other.test/",  # host check in combination
])
def test_invalid_sitemap_url_is_input_error(tmp_path, url):
    urls = ["https://example.test/", url] if url == "https://other.test/" else [url]
    with pytest.raises(ValueError):
        verify_sitemap_manifest(_manifest({"url": "https://example.test/", "indexable": True}), _sitemap(tmp_path, *urls))


def test_alias_duplicate_sitemap_entries_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="Duplicate sitemap"):
        verify_sitemap_manifest(
            _manifest({"url": "https://example.test/", "indexable": True}),
            _sitemap(tmp_path, "https://EXAMPLE.test:443/%7E", "https://example.test/~"),
        )


def test_conflicting_manifest_indexability_intent_rejected(tmp_path):
    with pytest.raises(ValueError, match="conflict"):
        verify_sitemap_manifest(_manifest(
            {"url": "https://example.test/alias", "canonical": "https://example.test/target", "indexable": True},
            {"url": "https://example.test/target", "indexable": False},
        ), _sitemap(tmp_path, "https://example.test/target"))


def test_manifest_invalid_indexable_type_rejected(tmp_path):
    with pytest.raises(ValueError, match="indexable"):
        verify_sitemap_manifest(
            _manifest({"url": "https://example.test/", "indexable": 1}), _sitemap(tmp_path),
        )


def test_bounded_sitemap_input(tmp_path):
    path = tmp_path / "huge.xml"
    path.write_bytes(b"x" * 4_000_001)
    with pytest.raises(ValueError, match="exceeds"):
        verify_sitemap_manifest(_manifest({"url": "https://example.test/", "indexable": True}), path)


@pytest.mark.parametrize("value", [None, 2, {}, [], "ftp://example.test/"])
def test_contract_url_invalid_type_fails_closed(tmp_path, value):
    with pytest.raises(ValueError, match="contract URL"):
        verify_sitemap_manifest(_manifest({"url": value}), _sitemap(tmp_path))


def test_canonical_url_invalid_type_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="canonical URL"):
        verify_sitemap_manifest(
            _manifest({"url": "https://example.test/", "canonical": 13}), _sitemap(tmp_path),
        )


def test_xml_declared_non_utf8_is_rejected_even_if_ascii_only(tmp_path):
    xml = ('<?xml version="1.0" encoding="ISO-8859-1"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>')
    with pytest.raises(ValueError, match="UTF-8"):
        verify_sitemap_manifest(
            _manifest({"url": "https://example.test/", "indexable": True}), _sitemap(tmp_path, xml=xml),
        )


@pytest.mark.parametrize("indexable,sitemap_urls,exit_code", [
    (True, ["https://example.test/"], 0),
    (True, ["https://example.test/elsewhere"], 1),
    (False, ["https://example.test/"], 1),
])
def test_cli_json_exits_and_report(tmp_path, capsys, indexable, sitemap_urls, exit_code):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest({
        "url": "https://example.test/", "indexable": indexable,
    })), encoding="utf-8")
    rc = main([str(manifest), str(_sitemap(tmp_path, *sitemap_urls)), "--json"])
    assert rc == exit_code
    assert json.loads(capsys.readouterr().out)["status"] == ("pass" if rc == 0 else "fail")


def test_cli_invalid_sitemap_returns_structured_input_error(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest({"url": "https://example.test/"})), encoding="utf-8")
    rc = main([str(manifest), str(_sitemap(tmp_path, xml="not xml")), "--json"])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"
