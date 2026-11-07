"""Cross-page hreflang return checks use only locally supplied build HTML."""

import json
import socket

from searcharis.site_ci import verify_manifest
from searcharis.site_cli import main

BASE = "https://example.com/"


def _alternate(language, target):
    return f'<link rel="alternate" hreflang="{language}" href="{target}">'


def _build(tmp_path):
    for language in ("en", "sl"):
        path = tmp_path / language / "index.html"
        path.parent.mkdir()
        path.write_text(
            "<!doctype html><html><head><title>Page</title>"
            + f'<link rel="canonical" href="{BASE}{language}/">'
            + _alternate("en", "/en/")
            + _alternate("sl", "/sl/")
            + "</head><body><h1>Page</h1></body></html>",
            encoding="utf-8",
        )
    return {
        "version": 1,
        "pages": [
            {
                "path": f"{language}/index.html",
                "contract": {"url": f"{BASE}{language}/", "title_present": True},
            }
            for language in ("sl", "en")
        ],
    }


def _replace(tmp_path, language, old, new):
    path = tmp_path / language / "index.html"
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def _issues(report, language):
    page = next(page for page in report["pages"] if page["path"] == f"{language}/index.html")
    return page["issues"]


def _check(manifest, root):
    return verify_manifest(manifest, root, check_hreflang_reciprocity=True)


def test_valid_bilingual_links_are_bidirectional_and_deterministic(tmp_path):
    manifest = _build(tmp_path)
    first = _check(manifest, tmp_path)
    second = _check(manifest, tmp_path)
    assert first == second
    assert first["status"] == "pass"
    assert first["hreflang_reciprocity"] == {"checked_pairs": 2, "unverified_targets": []}
    assert first["summary"] == {"checked": 2, "passed": 2, "failed": 0}
    assert all("observed_hreflang" not in page for page in first["pages"])


def test_missing_return_link_fails_source_that_needs_it(tmp_path):
    manifest = _build(tmp_path)
    _replace(tmp_path, "sl", _alternate("en", "/en/"), "")
    result = _check(manifest, tmp_path)
    assert result["status"] == "fail"
    assert result["summary"]["failed"] == 1
    assert _issues(result, "en") == [{
        "code": "hreflang_return:en",
        "target": f"{BASE}sl/",
        "expected": [f"{BASE}en/"],
        "actual": [],
    }]


def test_missing_self_label_fails_even_when_other_page_links_back(tmp_path):
    manifest = _build(tmp_path)
    _replace(tmp_path, "en", _alternate("en", "/en/"), "")
    result = _check(manifest, tmp_path)
    assert result["status"] == "fail"
    assert "hreflang_self_missing" in {item["code"] for item in _issues(result, "en")}


def test_wrong_and_duplicate_return_links_fail(tmp_path):
    manifest = _build(tmp_path)
    _replace(tmp_path, "sl", _alternate("en", "/en/"), _alternate("en", "/wrong/"))
    result = _check(manifest, tmp_path)
    assert _issues(result, "en")[0]["actual"] == [f"{BASE}wrong/"]
    assert result["hreflang_reciprocity"]["unverified_targets"] == [
        {"path": "sl/index.html", "target": f"{BASE}wrong/"}
    ]
    _replace(tmp_path, "sl", _alternate("en", "/wrong/"), _alternate("en", "/en/") * 2)
    result = _check(manifest, tmp_path)
    assert _issues(result, "en")[0]["actual"] == [f"{BASE}en/"] * 2


def test_unlisted_peers_are_reported_but_not_claimed_as_checked(tmp_path):
    manifest = _build(tmp_path)
    _replace(tmp_path, "en", "</head>", _alternate("de", "https://elsewhere.test/de/") + "</head>")
    report = _check(manifest, tmp_path)
    assert report["status"] == "pass"
    assert report["hreflang_reciprocity"] == {
        "checked_pairs": 2,
        "unverified_targets": [{"path": "en/index.html", "target": "https://elsewhere.test/de/"}],
    }


def test_multiple_self_locale_labels_all_require_return_links(tmp_path):
    manifest = _build(tmp_path)
    for language in ("en", "sl"):
        _replace(tmp_path, language, "</head>", _alternate("en-US", "/en/") + "</head>")
    assert _check(manifest, tmp_path)["status"] == "pass"
    _replace(tmp_path, "sl", _alternate("en-US", "/en/"), "")
    result = _check(manifest, tmp_path)
    assert "hreflang_return:en-us" in {item["code"] for item in _issues(result, "en")}


def test_case_insensitive_labels_and_x_default_are_supported(tmp_path):
    manifest = _build(tmp_path)
    for language in ("en", "sl"):
        _replace(tmp_path, language, 'hreflang="en"', 'hreflang="EN"')
        _replace(tmp_path, language, "</head>", _alternate("x-default", "/en/") + "</head>")
    assert _check(manifest, tmp_path)["status"] == "pass"


def test_opt_in_preserves_original_manifest_behavior(tmp_path):
    manifest = _build(tmp_path)
    _replace(tmp_path, "sl", _alternate("en", "/en/"), "")
    unchanged = verify_manifest(manifest, tmp_path)
    assert unchanged["status"] == "pass"
    assert "hreflang_reciprocity" not in unchanged


def test_cli_json_and_human_exit_codes(tmp_path, capsys):
    manifest = _build(tmp_path)
    file = tmp_path / "manifest.json"
    file.write_text(json.dumps(manifest), encoding="utf-8")
    assert main([str(file), str(tmp_path), "--check-hreflang-reciprocity"]) == 0
    assert "2 internal pairs checked" in capsys.readouterr().out
    _replace(tmp_path, "sl", _alternate("en", "/en/"), "")
    assert main([str(file), str(tmp_path), "--check-hreflang-reciprocity", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fail"
    assert _issues(report, "en")[0]["code"] == "hreflang_return:en"


def test_reciprocal_check_never_fetches_target_urls(tmp_path, monkeypatch):
    manifest = _build(tmp_path)

    def reject_network(*_args, **_kwargs):
        raise AssertionError("Offline build verification must not use the network")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    assert _check(manifest, tmp_path)["status"] == "pass"


def test_complete_local_locale_cluster_passes_and_is_deterministic(tmp_path):
    manifest = _build(tmp_path)
    first = verify_manifest(manifest, tmp_path, check_hreflang_clusters=True)
    assert first == verify_manifest(manifest, tmp_path, check_hreflang_clusters=True)
    assert first["status"] == "pass"
    assert first["hreflang_clusters"] == {"checked_pairs": 2, "unverified_targets": []}


def test_missing_local_locale_on_one_peer_fails_strict_cluster_not_reciprocity(tmp_path):
    manifest = _build(tmp_path)
    _replace(tmp_path, "en", "</head>", _alternate("de", "/de/") + "</head>")
    de = tmp_path / "de" / "index.html"
    de.parent.mkdir()
    de.write_text(
        "<!doctype html><html><head><title>Deutsch</title>"
        + _alternate("de", "/de/") + _alternate("en", "/en/")
        + "</head><body><h1>Deutsch</h1></body></html>",
        encoding="utf-8",
    )
    manifest["pages"].append({
        "path": "de/index.html", "contract": {"url": f"{BASE}de/", "title_present": True}
    })
    assert _check(manifest, tmp_path)["status"] == "pass"
    strict = verify_manifest(manifest, tmp_path, check_hreflang_clusters=True)
    assert strict["status"] == "fail"
    assert any(issue["code"] == "hreflang_cluster_mismatch:de" for issue in _issues(strict, "en"))


def test_conflicting_local_x_default_is_reported(tmp_path):
    manifest = _build(tmp_path)
    _replace(tmp_path, "en", "</head>", _alternate("x-default", "/en/") + "</head>")
    _replace(tmp_path, "sl", "</head>", _alternate("x-default", "/sl/") + "</head>")
    assert _check(manifest, tmp_path)["status"] == "pass"
    strict = verify_manifest(manifest, tmp_path, check_hreflang_clusters=True)
    assert "hreflang_cluster_mismatch:x-default" in {
        issue["code"] for issue in _issues(strict, "en")
    }


def test_duplicate_locale_on_every_page_fails_strict_policy(tmp_path):
    manifest = _build(tmp_path)
    for language in ("en", "sl"):
        _replace(tmp_path, language, "</head>", _alternate("en", "/en/") + "</head>")
    strict = verify_manifest(manifest, tmp_path, check_hreflang_clusters=True)
    assert strict["status"] == "fail"
    assert any(issue["code"] == "hreflang_cluster_duplicate:en" for issue in _issues(strict, "en"))


def test_external_locale_targets_are_explicitly_unverified_not_assumed_missing(tmp_path):
    manifest = _build(tmp_path)
    _replace(tmp_path, "en", "</head>", _alternate("de", "https://other.test/de/") + "</head>")
    strict = verify_manifest(manifest, tmp_path, check_hreflang_clusters=True)
    assert strict["status"] == "pass"
    assert strict["hreflang_clusters"]["unverified_targets"] == [
        {"path": "en/index.html", "language": "de", "target": "https://other.test/de/"}
    ]


def test_blank_hreflang_href_fails_opt_in_checks_without_contract_assertion(tmp_path):
    manifest = _build(tmp_path)
    _replace(tmp_path, "en", "</head>", _alternate("fr", "") + "</head>")
    assert verify_manifest(manifest, tmp_path)["status"] == "pass"
    for option in ("check_hreflang_reciprocity", "check_hreflang_clusters"):
        report = verify_manifest(manifest, tmp_path, **{option: True})
        assert report["status"] == "fail"
        assert any(issue["code"] == "hreflang_empty_href:fr" for issue in _issues(report, "en"))


def test_cluster_cli_exit_status_and_offline_guarantee(tmp_path, capsys, monkeypatch):
    manifest = _build(tmp_path)
    file = tmp_path / "manifest.json"
    file.write_text(json.dumps(manifest), encoding="utf-8")

    def reject_network(*_args, **_kwargs):
        raise AssertionError("No remote site requests permitted")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    assert main([str(file), str(tmp_path), "--check-hreflang-clusters"]) == 0
    assert "2 directed peer pairs" in capsys.readouterr().out
    _replace(tmp_path, "sl", _alternate("en", "/en/"), "")
    assert main([str(file), str(tmp_path), "--check-hreflang-clusters", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "fail"
    assert any(issue["code"] == "hreflang_cluster_mismatch:en" for issue in _issues(result, "en"))
