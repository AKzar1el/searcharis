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


@pytest.mark.parametrize("alias", [
    "https://EXAMPLE.com/sl/",
    "https://example.com:443/sl/",
    "https://EXAMPLE.com:443/sl/",
])
def test_canonical_target_alias_cannot_hide_local_nonself_canonical(tmp_path, alias):
    manifest = _build(tmp_path)
    _change(tmp_path, "en", [alias])
    _change(tmp_path, "sl", ["/wrong/"])

    result = _run(manifest, tmp_path)
    assert result["status"] == "fail"
    assert _issues(result, "en")[0]["code"] == "canonical_target_not_self"
    assert result["canonical_consistency"]["internal_targets_checked"] == 1
    assert {"path": "en/index.html", "target": alias} not in (
        result["canonical_consistency"]["unverified_targets"]
    )


@pytest.mark.parametrize("declared_target,observed_alias", [
    ("https://example.com/~sl/", "https://example.com/%7esl/"),
    ("https://example.com/sl/?q=A", "https://example.com/sl/?q=%41"),
    ("https://example.com/a%2Fb/", "https://example.com/a%2fb/"),
])
def test_percent_encoded_alias_cannot_hide_nonself_local_canonical(
    tmp_path, declared_target, observed_alias
):
    manifest = _build(tmp_path)
    manifest["pages"][0]["contract"]["url"] = declared_target
    _change(tmp_path, "en", [observed_alias])
    _change(tmp_path, "sl", ["/wrong/"])

    result = _run(manifest, tmp_path)
    assert result["status"] == "fail"
    assert _issues(result, "en")[0]["code"] == "canonical_target_not_self"
    assert result["canonical_consistency"]["internal_targets_checked"] == 1
    assert {"path": "en/index.html", "target": observed_alias} not in (
        result["canonical_consistency"]["unverified_targets"]
    )


def test_percent_encoded_local_alias_accepts_equivalent_self_canonical(tmp_path):
    manifest = _build(tmp_path)
    manifest["pages"][0]["contract"]["url"] = "https://example.com/~sl/"
    _change(tmp_path, "en", ["https://example.com/%7Esl/"])
    _change(tmp_path, "sl", ["https://example.com/%7esl/"])

    result = _run(manifest, tmp_path)
    assert result["status"] == "pass"
    assert result["canonical_consistency"] == {
        "internal_targets_checked": 1, "unverified_targets": [],
    }


def test_percent_encoded_reserved_delimiter_remains_a_distinct_route(tmp_path):
    manifest = _build(tmp_path)
    manifest["pages"][0]["contract"]["url"] = "https://example.com/a%2Fb/"
    _change(tmp_path, "en", ["https://example.com/a/b/"])
    _change(tmp_path, "sl", ["/wrong/"])

    result = _run(manifest, tmp_path)
    assert result["status"] == "pass"
    assert result["canonical_consistency"]["internal_targets_checked"] == 0
    assert {"path": "en/index.html", "target": "https://example.com/a/b/"} in (
        result["canonical_consistency"]["unverified_targets"]
    )


@pytest.mark.parametrize("alias", [
    "https://EXAMPLE.com/sl/",
    "https://example.com:443/sl/",
])
def test_canonical_local_target_alias_accepts_equivalent_self_link(tmp_path, alias):
    manifest = _build(tmp_path)
    _change(tmp_path, "en", [alias])
    result = _run(manifest, tmp_path)

    assert result["status"] == "pass"
    assert result["canonical_consistency"] == {
        "internal_targets_checked": 1, "unverified_targets": [],
    }


def test_same_page_canonical_hostname_alias_is_not_external(tmp_path):
    manifest = _build(tmp_path)
    _change(tmp_path, "en", ["https://EXAMPLE.com/en/"])
    result = _run(manifest, tmp_path)

    assert result["status"] == "pass"
    assert result["canonical_consistency"] == {
        "internal_targets_checked": 0, "unverified_targets": [],
    }


@pytest.mark.parametrize("scheme,default_port", [
    ("https", 443), ("http", 80),
])
def test_root_path_and_default_port_aliases_resolve_local_canonical(
    tmp_path, scheme, default_port
):
    manifest = _build(tmp_path)
    manifest["pages"][0]["contract"]["url"] = f"{scheme}://example.com"
    manifest["pages"][1]["contract"]["url"] = f"{scheme}://example.com/en/"
    _change(tmp_path, "en", [f"{scheme}://EXAMPLE.com:{default_port}/"])
    _change(tmp_path, "sl", [f"{scheme}://example.com/"])

    result = _run(manifest, tmp_path)
    assert result["status"] == "pass"
    assert result["canonical_consistency"] == {
        "internal_targets_checked": 1, "unverified_targets": [],
    }


@pytest.mark.parametrize("different_target", [
    "https://EXAMPLE.com/SL/",
    "https://EXAMPLE.com/sl/?q=ONE",
    "https://EXAMPLE.com:8443/sl/",
    "http://EXAMPLE.com/sl/",
])
def test_canonical_cross_page_lookup_preserves_distinct_routes(tmp_path, different_target):
    manifest = _build(tmp_path)
    _change(tmp_path, "en", [different_target])
    result = _run(manifest, tmp_path)

    assert result["status"] == "pass"
    assert result["canonical_consistency"] == {
        "internal_targets_checked": 0,
        "unverified_targets": [{
            "path": "en/index.html", "target": different_target,
        }],
    }


def test_cli_reports_cross_page_canonical_host_alias_failure(tmp_path, capsys):
    manifest = _build(tmp_path)
    _change(tmp_path, "en", ["https://EXAMPLE.com/sl/"])
    _change(tmp_path, "sl", ["/wrong/"])
    file = tmp_path / "manifest.json"
    file.write_text(json.dumps(manifest), encoding="utf-8")

    assert main([str(file), str(tmp_path), "--check-canonical-consistency", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert _issues(report, "en")[0]["code"] == "canonical_target_not_self"


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
        (["https://example.com/en/#section"], "canonical_invalid_url"),
        (["https://example.com/en/#"], "canonical_invalid_url"),
        (["/en/#"], "canonical_invalid_url"),
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
