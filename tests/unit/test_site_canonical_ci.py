"""Cross-page canonical checks use only actual locally built HTML links."""

import json
import socket

import pytest

from searcharis.site_ci import verify_manifest
from searcharis.site_cli import main

BASE = "https://example.com/"


def _html(canonicals):
    links = "".join(f'<link rel="canonical" href="{href}">' for href in canonicals)
    return f"<!doctype html><html><head><title>Page</title>{links}</head><body></body></html>"


def _build(tmp_path):
    for language in ("en", "sl"):
        path = tmp_path / language / "index.html"
        path.parent.mkdir()
        path.write_text(_html([f"/{language}/"]), encoding="utf-8")
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


def _change(tmp_path, language, canonicals):
    (tmp_path / language / "index.html").write_text(
        _html(canonicals), encoding="utf-8"
    )


def _run(manifest, tmp_path):
    return verify_manifest(
        manifest, tmp_path, check_canonical_consistency=True,
        require_full_coverage=True,
    )


def _issues(result, language):
    return next(
        item["issues"] for item in result["pages"]
        if item["path"] == f"{language}/index.html"
    )


def test_self_canonicals_pass_and_observed_links_stay_internal(tmp_path):
    manifest = _build(tmp_path)
    first = _run(manifest, tmp_path)
    assert first == _run(manifest, tmp_path)
    assert first["status"] == "pass"
    assert first["canonical_consistency"] == {
        "internal_targets_checked": 0, "unverified_targets": [],
    }
    assert all("observed_canonical" not in page for page in first["pages"])


def test_alias_to_local_self_canonical_target_is_valid(tmp_path):
    manifest = _build(tmp_path)
    _change(tmp_path, "en", ["/sl/"])
    result = _run(manifest, tmp_path)
    assert result["status"] == "pass"
    assert result["canonical_consistency"]["internal_targets_checked"] == 1


def test_local_canonical_cycles_fail_on_both_sources(tmp_path):
    manifest = _build(tmp_path)
    _change(tmp_path, "en", ["/sl/"])
    _change(tmp_path, "sl", ["/en/"])
    result = _run(manifest, tmp_path)
    assert result["summary"]["failed"] == 2
    assert result["canonical_consistency"]["internal_targets_checked"] == 2
    for language in ("en", "sl"):
        assert _issues(result, language)[0]["code"] == "canonical_target_not_self"


@pytest.mark.parametrize(
    ("links", "code"),
    [
        ([], "canonical_count"),
        (["/en/", "/sl/"], "canonical_count"),
        ([""], "canonical_invalid_url"),
        (["javascript:alert(1)"], "canonical_invalid_url"),
        (["https://[broken"], "canonical_invalid_url"),
        (["https://example.com:badport/en/"], "canonical_invalid_url"),
        (["https://example.com:/en/"], "canonical_invalid_url"),
        (["https://example.com/en/ bad"], "canonical_invalid_url"),
        ([r"https://example.com\en/"], "canonical_invalid_url"),
    ],
)
def test_missing_duplicate_and_invalid_canonicals_fail(tmp_path, links, code):
    manifest = _build(tmp_path)
    _change(tmp_path, "en", links)
    result = _run(manifest, tmp_path)
    assert result["summary"]["failed"] == 1
    assert _issues(result, "en")[0]["code"] == code


def test_body_only_canonical_does_not_pass_cross_page_consistency(tmp_path, capsys):
    manifest = _build(tmp_path)
    path = tmp_path / "en" / "index.html"
    path.write_text(
        _html(["/en/"]).replace(
            '<link rel="canonical" href="/en/"></head><body>',
            '</head><body><link rel="canonical" href="/en/">',
        ),
        encoding="utf-8",
    )
    result = _run(manifest, tmp_path)
    assert result["status"] == "fail"
    assert _issues(result, "en")[0]["code"] == "canonical_count"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert main([str(manifest_path), str(tmp_path), "--check-canonical-consistency", "--json"]) == 1
    assert _issues(json.loads(capsys.readouterr().out), "en")[0]["code"] == "canonical_count"


def test_external_or_missing_manifest_target_is_unverified_not_passed_as_checked(
    tmp_path, monkeypatch,
):
    manifest = _build(tmp_path)
    _change(tmp_path, "en", ["https://elsewhere.test/en/"])

    def reject_network(*_args, **_kwargs):
        raise AssertionError("Offline canonical checker must not fetch any URL")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    result = _run(manifest, tmp_path)
    assert result["status"] == "pass"
    assert result["canonical_consistency"] == {
        "internal_targets_checked": 0,
        "unverified_targets": [
            {"path": "en/index.html", "target": "https://elsewhere.test/en/"}
        ],
    }


def test_base_href_changes_offline_cross_page_canonical_target(tmp_path, capsys):
    manifest = _build(tmp_path)
    path = tmp_path / "en" / "index.html"
    path.write_text(
        '<html><head><title>Page</title><base href="https://elsewhere.test/articles/">'
        '<link rel="canonical" href="story.html"></head><body></body></html>',
        encoding="utf-8",
    )
    result = _run(manifest, tmp_path)
    assert result["canonical_consistency"]["unverified_targets"] == [
        {"path": "en/index.html", "target": "https://elsewhere.test/articles/story.html"}
    ]
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    assert main([str(manifest_file), str(tmp_path), "--check-canonical-consistency", "--json"]) == 0
    cli_result = json.loads(capsys.readouterr().out)
    assert cli_result["canonical_consistency"]["unverified_targets"] == result["canonical_consistency"]["unverified_targets"]


def test_duplicate_href_uses_browser_first_value_for_local_target(tmp_path, capsys):
    manifest = _build(tmp_path)
    (tmp_path / "en" / "index.html").write_text(
        '<html><head><title>Page</title>'
        '<link rel="canonical" href="/sl/" href="/en/">'
        '</head><body></body></html>',
        encoding="utf-8",
    )
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    assert main([str(manifest_file), str(tmp_path), "--check-canonical-consistency", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert report["canonical_consistency"]["internal_targets_checked"] == 1


def test_opt_in_preserves_existing_checker_default(tmp_path):
    manifest = _build(tmp_path)
    _change(tmp_path, "en", [])
    normal = verify_manifest(manifest, tmp_path)
    assert normal["status"] == "pass"
    assert "canonical_consistency" not in normal
    assert _run(manifest, tmp_path)["status"] == "fail"


def test_cli_human_and_json_findings(tmp_path, capsys):
    manifest = _build(tmp_path)
    file = tmp_path / "manifest.json"
    file.write_text(json.dumps(manifest), encoding="utf-8")
    assert main([
        str(file), str(tmp_path), "--check-canonical-consistency",
        "--require-full-coverage",
    ]) == 0
    assert "Canonicals: 0 internal targets checked" in capsys.readouterr().out
    _change(tmp_path, "en", ["/sl/"])
    _change(tmp_path, "sl", ["/en/"])
    assert main([str(file), str(tmp_path), "--check-canonical-consistency", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fail"
    assert _issues(report, "en")[0]["code"] == "canonical_target_not_self"
