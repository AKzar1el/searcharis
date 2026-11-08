"""Offline multi-page artifact verification and coverage guarantees."""

import json
import socket

import pytest

from searcharis.site_ci import verify_manifest
from searcharis.site_cli import main

BASE = "https://example.com/"


def _page(url):
    return (
        "<!doctype html><html><head><title>Page</title>"
        f'<link rel="canonical" href="{url}">'
        "</head><body><h1>Heading</h1></body></html>"
    )


def _setup(tmp_path):
    for locale in ("en", "sl"):
        path = tmp_path / locale / "index.html"
        path.parent.mkdir()
        path.write_text(_page(f"{BASE}{locale}/"), encoding="utf-8")
    manifest = {
        "version": 1,
        "pages": [
            {
                "path": f"{locale}/index.html",
                "contract": {
                    "url": f"{BASE}{locale}/",
                    "title_present": True,
                    "canonical": f"{BASE}{locale}/",
                    "h1_min": 1,
                },
            }
            for locale in ("sl", "en")
        ],
    }
    return manifest


def test_two_pages_pass_with_deterministic_path_order(tmp_path):
    report = verify_manifest(_setup(tmp_path), tmp_path, require_full_coverage=True)
    assert report["status"] == "pass"
    assert report["summary"] == {"checked": 2, "passed": 2, "failed": 0}
    assert report["coverage"]["unlisted"] == []
    assert [page["path"] for page in report["pages"]] == ["en/index.html", "sl/index.html"]


def test_an_actual_regression_blocks_even_partial_coverage(tmp_path):
    manifest = _setup(tmp_path)
    page = tmp_path / "sl" / "index.html"
    page.write_text(_page(f"{BASE}wrong/"), encoding="utf-8")
    report = verify_manifest(manifest, tmp_path)
    assert report["status"] == "fail"
    assert report["summary"] == {"checked": 2, "passed": 1, "failed": 1}
    assert [issue["code"] for issue in report["pages"][1]["issues"]] == ["canonical"]


def test_template_only_heading_cannot_pass_static_build_ci(tmp_path, capsys):
    manifest = _setup(tmp_path)
    page = tmp_path / "en" / "index.html"
    page.write_text(
        _page(f"{BASE}en/").replace(
            "<h1>Heading</h1>", "<template><h1>Not rendered</h1></template>"
        ),
        encoding="utf-8",
    )
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert main([str(path), str(tmp_path), "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "fail"
    assert [issue["code"] for issue in result["pages"][0]["issues"]] == ["h1_min"]


def test_unlisted_pages_are_visible_and_optionally_fatal(tmp_path):
    manifest = _setup(tmp_path)
    extra = tmp_path / "extra.html"
    extra.write_text(_page(f"{BASE}extra/"), encoding="utf-8")
    permissive = verify_manifest(manifest, tmp_path)
    strict = verify_manifest(manifest, tmp_path, require_full_coverage=True)
    assert permissive["status"] == "pass"
    assert strict["status"] == "fail"
    assert strict["coverage"]["discovered"] == 3
    assert strict["coverage"]["checked"] == 2
    assert strict["coverage"]["unlisted"] == ["extra.html"]


@pytest.mark.parametrize(
    "path",
    [
        "../outside.html",
        "/tmp/inside.html",
        "C:/windows.html",
        "en\\index.html",
        "en//index.html",
        "en/./index.html",
        "en/../sl/index.html",
        "en/index.txt",
        "",
    ],
)
def test_unsafe_or_non_html_paths_rejected(tmp_path, path):
    manifest = _setup(tmp_path)
    manifest["pages"][0]["path"] = path
    with pytest.raises(ValueError, match="path|HTML"):
        verify_manifest(manifest, tmp_path)


def test_duplicate_page_paths_rejected_case_insensitively(tmp_path):
    manifest = _setup(tmp_path)
    manifest["pages"][1]["path"] = manifest["pages"][0]["path"].upper()
    with pytest.raises(ValueError, match="Duplicate manifest page path"):
        verify_manifest(manifest, tmp_path)


@pytest.mark.parametrize("wrong_path", ["EN/index.html", "en/INDEX.HTML"])
@pytest.mark.parametrize("strict", [False, True])
def test_manifest_rejects_case_mismatched_artifact_path(tmp_path, wrong_path, strict):
    manifest = _setup(tmp_path)
    manifest["pages"][1]["path"] = wrong_path
    # On case-insensitive hosts the wrong-cased path opens successfully, but
    # would be missing after deploying the same build to a case-sensitive host.
    with pytest.raises(ValueError):
        verify_manifest(manifest, tmp_path, require_full_coverage=strict)


def test_site_cli_rejects_case_mismatched_artifact_path(tmp_path, capsys):
    manifest = _setup(tmp_path)
    manifest["pages"][1]["path"] = "EN/index.html"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert main([str(path), str(tmp_path), "--require-full-coverage", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_duplicate_contract_urls_rejected(tmp_path):
    manifest = _setup(tmp_path)
    manifest["pages"][1]["contract"]["url"] = manifest["pages"][0]["contract"]["url"]
    with pytest.raises(ValueError, match="Duplicate contract URL"):
        verify_manifest(manifest, tmp_path)


def test_site_cli_rejects_invalid_url_before_cross_page_checks(tmp_path, capsys):
    manifest = _setup(tmp_path)
    manifest["pages"][0]["contract"]["url"] = "https://example.com:invalid/sl/"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert main([str(path), str(tmp_path), "--json", "--check-canonical-consistency"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "error"
    assert "HTTP(S) URL" in report["error"]


def test_site_cli_rejects_invalid_expected_hreflang_url(tmp_path, capsys):
    manifest = _setup(tmp_path)
    manifest["pages"][0]["contract"]["hreflang"] = {"sl": "https://example.com:invalid/sl/"}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert main([str(path), str(tmp_path), "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["status"] == "error"
    assert "hreflang" in error["error"]


def test_site_cli_rejects_empty_jsonld_type_assertion(tmp_path, capsys):
    manifest = _setup(tmp_path)
    manifest["pages"][0]["contract"] = {
        "url": f"{BASE}sl/", "jsonld_types": [],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert main([str(path), str(tmp_path), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "error"
    assert "jsonld_types" in report["error"]


def test_invalid_manifest_shapes_or_missing_page_fail_closed(tmp_path):
    manifest = _setup(tmp_path)
    for invalid in (
        {"version": True, "pages": manifest["pages"]},
        {"version": 2, "pages": manifest["pages"]},
        {"version": 1, "pages": []},
        {"version": 1, "pages": manifest["pages"], "extra": "ignore"},
    ):
        with pytest.raises(ValueError):
            verify_manifest(invalid, tmp_path)
    manifest["pages"][0]["path"] = "missing.html"
    with pytest.raises(ValueError, match="Missing or unsafe"):
        verify_manifest(manifest, tmp_path)


def test_oversized_file_is_input_error(tmp_path):
    manifest = _setup(tmp_path)
    (tmp_path / "en" / "index.html").write_bytes(b"a" * 4_000_001)
    with pytest.raises(ValueError, match="exceeds"):
        verify_manifest(manifest, tmp_path)


def test_cli_exits_and_machine_readable_errors(tmp_path, capsys):
    manifest = _setup(tmp_path)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert main([str(path), str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"
    (tmp_path / "extra.html").write_text(_page(f"{BASE}extra/"), encoding="utf-8")
    assert main([str(path), str(tmp_path), "--require-full-coverage"]) == 1
    assert "Unlisted HTML" in capsys.readouterr().out
    path.write_text('{"version": 0}', encoding="utf-8")
    assert main([str(path), str(tmp_path), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


@pytest.mark.parametrize(
    "make_duplicate",
    [
        lambda raw: raw.replace('"version": 1', '"version": 0, "version": 1', 1),
        lambda raw: raw.replace(
            '"title_present": true', '"title_present": false, "title_present": true', 1
        ),
    ],
    ids=["top-level-version", "nested-contract-assertion"],
)
def test_manifest_cli_rejects_duplicate_json_keys(tmp_path, capsys, make_duplicate):
    manifest = _setup(tmp_path)
    path = tmp_path / "manifest.json"
    path.write_text(make_duplicate(json.dumps(manifest)), encoding="utf-8")

    assert main([str(path), str(tmp_path), "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["status"] == "error"
    assert "Duplicate manifest JSON key" in error["error"]


def test_declared_url_never_fetched(tmp_path, monkeypatch):
    manifest = _setup(tmp_path)

    def blocked_network(*_args, **_kwargs):
        raise AssertionError("Network use forbidden for offline contracts")

    monkeypatch.setattr(socket, "create_connection", blocked_network)
    assert verify_manifest(manifest, tmp_path)["status"] == "pass"
