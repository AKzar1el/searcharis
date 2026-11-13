"""Offline sitemap intent checks: explicit policy, no network or hosting inference."""

import gzip
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


def _sitemap(tmp_path, *urls, xml=None, filename="sitemap.xml"):
    payload = xml if xml is not None else (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>{escape(url)}</loc></url>" for url in urls)
        + "</urlset>"
    )
    path = tmp_path / filename
    path.write_text(payload, encoding="utf-8")
    return path


def _gzip_sitemap(path):
    compressed = path.with_suffix(path.suffix + ".gz")
    compressed.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    return compressed


def test_explicit_local_gzip_urlset_passes(tmp_path):
    manifest = _manifest({"url": "https://example.test/", "indexable": True})
    raw = _sitemap(tmp_path, "https://example.test/")
    report = verify_sitemap_manifest(manifest, _gzip_sitemap(raw))
    assert report["status"] == "pass"
    assert report["summary"]["sitemap_urls"] == 1


def test_mixed_gzip_and_plain_shards_detect_noindex(tmp_path):
    manifest = _manifest(
        {"url": "https://example.test/", "indexable": True},
        {"url": "https://example.test/noindex", "indexable": False},
    )
    plain = _sitemap(tmp_path, "https://example.test/", filename="first.xml")
    compressed = _gzip_sitemap(_sitemap(
        tmp_path, "https://example.test/noindex", filename="second.xml",
    ))
    report = verify_sitemap_manifest(manifest, [plain, compressed])
    assert report["status"] == "fail"
    assert report["present_excluded"] == ["https://example.test/noindex"]


@pytest.mark.parametrize("payload", [b"not gzip", gzip.compress(b"<xml/>")[:-5]])
def test_corrupt_or_truncated_gzip_is_input_error(tmp_path, payload):
    compressed = tmp_path / "broken.xml.gz"
    compressed.write_bytes(payload)
    manifest = _manifest({"url": "https://example.test/", "indexable": True})
    with pytest.raises(ValueError, match="gzip"):
        verify_sitemap_manifest(manifest, compressed)


def test_gzip_inflated_bytes_cannot_exceed_shard_limit(tmp_path, monkeypatch):
    import searcharis.sitemap_ci as sitemap_ci

    raw = _sitemap(tmp_path, *(["https://example.test/long"] * 20))
    packed = _gzip_sitemap(raw)
    assert packed.stat().st_size < raw.stat().st_size
    monkeypatch.setattr(sitemap_ci, "_MAX_SITEMAP_BYTES", packed.stat().st_size + 1)
    with pytest.raises(ValueError, match="Sitemap exceeds"):
        verify_sitemap_manifest(
            _manifest({"url": "https://example.test/", "indexable": True}), packed,
        )


def test_gzip_compressed_input_is_bounded(tmp_path, monkeypatch):
    import searcharis.sitemap_ci as sitemap_ci

    packed = _gzip_sitemap(_sitemap(tmp_path, "https://example.test/"))
    monkeypatch.setattr(sitemap_ci, "_MAX_SITEMAP_BYTES", packed.stat().st_size - 1)
    with pytest.raises(ValueError, match="Compressed sitemap exceeds"):
        verify_sitemap_manifest(
            _manifest({"url": "https://example.test/", "indexable": True}), packed,
        )


def test_aggregate_budget_counts_inflated_bytes(tmp_path, monkeypatch):
    import searcharis.sitemap_ci as sitemap_ci

    first = _sitemap(tmp_path, "https://example.test/one", filename="first.xml")
    second = _sitemap(tmp_path, "https://example.test/two", filename="second.xml")
    limit = first.stat().st_size + second.stat().st_size - 1
    monkeypatch.setattr(sitemap_ci, "_MAX_TOTAL_SITEMAP_BYTES", limit)
    with pytest.raises(ValueError, match="Combined.*bytes"):
        verify_sitemap_manifest(
            _manifest({"url": "https://example.test/one", "indexable": True}),
            [_gzip_sitemap(first), _gzip_sitemap(second)],
        )


def test_cli_corrupt_gzip_returns_structured_input_error(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest({
        "url": "https://example.test/", "indexable": True,
    })), encoding="utf-8")
    compressed = tmp_path / "broken.xml.gz"
    compressed.write_bytes(b"not gzip")
    assert main([str(manifest), str(compressed), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


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


def test_opt_in_full_indexability_policy_rejects_unasserted_reviewed_page(tmp_path):
    manifest = _manifest(
        {"url": "https://example.test/", "indexable": True},
        {"url": "https://example.test/draft"},
    )
    sitemap = _sitemap(tmp_path, "https://example.test/")
    assert verify_sitemap_manifest(manifest, sitemap)["status"] == "pass"
    with pytest.raises(ValueError, match="explicit indexability"):
        verify_sitemap_manifest(
            manifest, sitemap, require_indexability_coverage=True,
        )


def test_opt_in_full_indexability_policy_accepts_true_and_false(tmp_path):
    manifest = _manifest(
        {"url": "https://example.test/", "indexable": True},
        {"url": "https://example.test/draft", "indexable": False},
    )
    report = verify_sitemap_manifest(
        manifest, _sitemap(tmp_path, "https://example.test/"),
        require_indexability_coverage=True,
    )
    assert report["status"] == "pass"
    assert report["summary"]["unasserted_pages"] == 0


def test_strict_sitemap_cli_reports_missing_policy_as_input_error(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest(
        {"url": "https://example.test/", "indexable": True},
        {"url": "https://example.test/draft"},
    )), encoding="utf-8")
    sitemap = _sitemap(tmp_path, "https://example.test/")
    code = main([
        str(manifest), str(sitemap), "--require-indexability-coverage", "--json",
    ])
    assert code == 2
    assert "explicit indexability" in json.loads(capsys.readouterr().out)["error"]


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


def test_multiple_local_urlsets_combine_reviewed_indexability(tmp_path):
    manifest = _manifest(
        {"url": "https://example.test/first", "indexable": True},
        {"url": "https://example.test/second", "indexable": True},
        {"url": "https://example.test/noindex", "indexable": False},
    )
    first = _sitemap(tmp_path, "https://example.test/first", filename="first.xml")
    second = _sitemap(tmp_path, "https://example.test/second", filename="second.xml")
    assert verify_sitemap_manifest(manifest, first)["status"] == "fail"
    report = verify_sitemap_manifest(manifest, [first, second])
    assert report["status"] == "pass"
    assert report["summary"]["sitemap_urls"] == 2
    assert report["summary"]["required"] == 2


def test_multiple_local_urlsets_detect_noindex_on_later_file(tmp_path):
    manifest = _manifest({"url": "https://example.test/noindex", "indexable": False})
    first = _sitemap(tmp_path, "https://example.test/other", filename="first.xml")
    second = _sitemap(tmp_path, "https://example.test/noindex", filename="second.xml")
    report = verify_sitemap_manifest(manifest, [first, second])
    assert report["status"] == "fail"
    assert report["present_excluded"] == ["https://example.test/noindex"]


def test_cli_accepts_two_explicit_local_urlsets(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest(
        {"url": "https://example.test/first", "indexable": True},
        {"url": "https://example.test/second", "indexable": True},
    )), encoding="utf-8")
    first = _sitemap(tmp_path, "https://example.test/first", filename="first.xml")
    second = _sitemap(tmp_path, "https://example.test/second", filename="second.xml")
    rc = main([str(manifest), str(first), str(second), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["summary"]["sitemap_urls"] == 2


def test_duplicate_identity_across_sitemap_files_is_input_error(tmp_path):
    manifest = _manifest({"url": "https://example.test/~user", "indexable": True})
    first = _sitemap(tmp_path, "https://EXAMPLE.test/%7Euser", filename="first.xml")
    second = _sitemap(tmp_path, "https://example.test:443/~user", filename="second.xml")
    with pytest.raises(ValueError, match="Duplicate sitemap"):
        verify_sitemap_manifest(manifest, [first, second])


def test_malformed_second_sitemap_fails_closed(tmp_path):
    manifest = _manifest({"url": "https://example.test/", "indexable": True})
    first = _sitemap(tmp_path, "https://example.test/", filename="first.xml")
    second = _sitemap(tmp_path, xml="not xml", filename="broken.xml")
    with pytest.raises(ValueError, match="Invalid sitemap XML"):
        verify_sitemap_manifest(manifest, [first, second])


def test_sitemap_files_must_have_same_host(tmp_path):
    manifest = _manifest({"url": "https://example.test/", "indexable": True})
    first = _sitemap(tmp_path, "https://example.test/", filename="first.xml")
    second = _sitemap(tmp_path, "https://other.test/", filename="second.xml")
    with pytest.raises(ValueError, match="same host"):
        verify_sitemap_manifest(manifest, [first, second])


def test_repeated_sitemap_file_is_not_silently_counted_twice(tmp_path):
    manifest = _manifest({"url": "https://example.test/", "indexable": True})
    first = _sitemap(tmp_path, "https://example.test/")
    with pytest.raises(ValueError, match="Duplicate sitemap"):
        verify_sitemap_manifest(manifest, [first, first])


def test_too_many_sitemap_files_rejected_before_reading(tmp_path):
    manifest = _manifest({"url": "https://example.test/", "indexable": True})
    first = _sitemap(tmp_path, "https://example.test/")
    with pytest.raises(ValueError, match="at most 16"):
        verify_sitemap_manifest(manifest, [first] * 17)


def test_total_sitemap_url_limit_applies_across_shards(tmp_path, monkeypatch):
    import searcharis.sitemap_ci as sitemap_ci

    manifest = _manifest({"url": "https://example.test/one", "indexable": True})
    first = _sitemap(tmp_path, "https://example.test/one", filename="first.xml")
    second = _sitemap(tmp_path, "https://example.test/two", filename="second.xml")
    monkeypatch.setattr(sitemap_ci, "_MAX_TOTAL_SITEMAP_URLS", 1, raising=False)
    with pytest.raises(ValueError, match="Combined.*URLs"):
        verify_sitemap_manifest(manifest, [first, second])


def test_total_sitemap_byte_limit_applies_across_files(tmp_path, monkeypatch):
    import searcharis.sitemap_ci as sitemap_ci

    manifest = _manifest({"url": "https://example.test/one", "indexable": True})
    first = _sitemap(tmp_path, "https://example.test/one", filename="first.xml")
    second = _sitemap(tmp_path, "https://example.test/two", filename="second.xml")
    monkeypatch.setattr(
        sitemap_ci, "_MAX_TOTAL_SITEMAP_BYTES", first.stat().st_size + second.stat().st_size - 1,
    )
    with pytest.raises(ValueError, match="Combined.*bytes"):
        verify_sitemap_manifest(manifest, [first, second])


@pytest.mark.parametrize("reported_size", [1, 10_000_000])
def test_total_sitemap_byte_limit_uses_read_bytes_not_stale_stat(
    tmp_path, monkeypatch, reported_size,
):
    from pathlib import Path
    from types import SimpleNamespace

    import searcharis.sitemap_ci as sitemap_ci

    first = _sitemap(tmp_path, "https://example.test/one", filename="first.xml")
    second = _sitemap(tmp_path, "https://example.test/two", filename="second.xml")
    actual_bytes = first.stat().st_size + second.stat().st_size
    # A file can grow or shrink between a size probe and opening it.
    budget = actual_bytes if reported_size > actual_bytes else actual_bytes - 1
    monkeypatch.setattr(sitemap_ci, "_MAX_TOTAL_SITEMAP_BYTES", budget)
    real_stat = Path.stat

    def stale_stat(path, *args, **kwargs):
        if path in (first, second):
            return SimpleNamespace(st_size=reported_size)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stale_stat)
    manifest = _manifest({"url": "https://example.test/one", "indexable": True})
    if reported_size < actual_bytes:
        with pytest.raises(ValueError, match="Combined.*bytes"):
            verify_sitemap_manifest(manifest, [first, second])
    else:
        assert verify_sitemap_manifest(manifest, [first, second])["status"] == "pass"


def test_no_local_sitemap_files_is_input_error(tmp_path):
    manifest = _manifest({"url": "https://example.test/", "indexable": True})
    with pytest.raises(ValueError, match="at most 16"):
        verify_sitemap_manifest(manifest, [])


def test_cli_fails_closed_on_invalid_later_file(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest({
        "url": "https://example.test/", "indexable": True,
    })), encoding="utf-8")
    first = _sitemap(tmp_path, "https://example.test/", filename="first.xml")
    second = _sitemap(tmp_path, xml="<sitemapindex/>", filename="index.xml")
    assert main([str(manifest), str(first), str(second), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"
