"""Offline multi-page artifact verification and coverage guarantees."""

import json
import os
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


def test_manifest_reports_first_og_image_dimension_regressions(tmp_path):
    manifest = _setup(tmp_path)
    first = next(page for page in manifest["pages"] if page["path"] == "en/index.html")
    first["contract"].update({"og_image_width_equals": 1200, "og_image_height_equals": 630})
    path = tmp_path / "en/index.html"
    path.write_text(_page(f"{BASE}en/").replace(
        "</head>",
        '<meta property="og:image" content="https://cdn.example.com/social.jpg">'
        '<meta property="og:image:width" content="800">'
        '<meta property="og:image:height" content="630"></head>',
    ), encoding="utf-8")
    result = verify_manifest(manifest, tmp_path, require_full_coverage=True)
    assert result["status"] == "fail"
    assert result["summary"] == {"checked": 2, "passed": 1, "failed": 1}
    en = next(page for page in result["pages"] if page["path"] == "en/index.html")
    assert en["issues"] == [{
        "code": "og_image_width_equals", "expected": 1200, "actual": "800",
    }]
    path.write_text(path.read_text(encoding="utf-8").replace('content="800"', 'content="1200"'),
                    encoding="utf-8")
    assert verify_manifest(manifest, tmp_path, require_full_coverage=True)["status"] == "pass"


def test_manifest_detects_first_og_image_type_regression(tmp_path):
    manifest = _setup(tmp_path)
    first = next(page for page in manifest["pages"] if page["path"] == "en/index.html")
    first["contract"]["og_image_type_equals"] = "image/png"
    path = tmp_path / "en/index.html"
    path.write_text(_page(f"{BASE}en/").replace(
        "</head>",
        '<meta property="og:image" content="https://cdn.example.com/social.png">'
        '<meta property="og:image:type" content="image/jpeg"></head>',
    ), encoding="utf-8")
    result = verify_manifest(manifest, tmp_path, require_full_coverage=True)
    assert result["status"] == "fail"
    en = next(page for page in result["pages"] if page["path"] == "en/index.html")
    assert en["issues"] == [{
        "code": "og_image_type_equals", "expected": "image/png", "actual": "image/jpeg",
    }]
    path.write_text(path.read_text(encoding="utf-8").replace('image/jpeg', 'image/png'),
                    encoding="utf-8")
    assert verify_manifest(manifest, tmp_path, require_full_coverage=True)["status"] == "pass"


def test_manifest_detects_twitter_image_and_alt_presence_regressions(tmp_path):
    manifest = _setup(tmp_path)
    target = next(page for page in manifest["pages"] if page["path"] == "en/index.html")
    target["contract"].update({"twitter_image_present": True, "twitter_image_alt_present": True})
    artifact = tmp_path / "en/index.html"
    artifact.write_text(_page(f"{BASE}en/").replace(
        "</head>", '<meta name="twitter:image" content="/relative.png">'
        '<meta name="twitter:image:alt" content=""></head>',
    ), encoding="utf-8")
    result = verify_manifest(manifest, tmp_path, require_full_coverage=True)
    assert result["status"] == "fail"
    en = next(page for page in result["pages"] if page["path"] == "en/index.html")
    assert [issue["code"] for issue in en["issues"]] == [
        "twitter_image_presence", "twitter_image_alt_presence",
    ]
    artifact.write_text(_page(f"{BASE}en/").replace(
        "</head>", '<meta name="twitter:image" content="https://cdn.example.com/card.png">'
        '<meta name="twitter:image:alt" content="A descriptive card"></head>',
    ), encoding="utf-8")
    assert verify_manifest(manifest, tmp_path, require_full_coverage=True)["status"] == "pass"


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


def test_deep_jsonld_fails_static_build_cli_without_traceback(tmp_path, capsys):
    manifest = _setup(tmp_path)
    for entry in manifest["pages"]:
        if entry["path"] == "en/index.html":
            entry["contract"]["jsonld_types"] = ["WebPage"]
    nested = "[" * 6000 + '{"@type":"WebPage"}' + "]" * 6000
    page = tmp_path / "en" / "index.html"
    page.write_text(
        _page(f"{BASE}en/").replace(
            "</head>", f'<script type="application/ld+json">{nested}</script></head>'
        ),
        encoding="utf-8",
    )
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert main([str(path), str(tmp_path), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fail"
    en_page = next(entry for entry in report["pages"] if entry["path"] == "en/index.html")
    assert {issue["code"] for issue in en_page["issues"]} == {
        "jsonld_invalid", "jsonld_type_missing",
    }


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


@pytest.mark.parametrize("extension", [".htm", ".HTM"])
def test_unlisted_htm_artifacts_cannot_bypass_strict_coverage(tmp_path, extension):
    manifest = _setup(tmp_path)
    extra = tmp_path / f"legacy{extension}"
    extra.write_text(_page(f"{BASE}legacy/"), encoding="utf-8")

    permissive = verify_manifest(manifest, tmp_path)
    strict = verify_manifest(manifest, tmp_path, require_full_coverage=True)

    assert permissive["status"] == "pass"
    assert strict["status"] == "fail"
    assert strict["coverage"]["discovered"] == 3
    assert strict["coverage"]["unlisted"] == [extra.name]


def test_strict_coverage_rejects_symlinked_directory_instead_of_missing_its_pages(
    tmp_path, monkeypatch, capsys,
):
    manifest = _setup(tmp_path)
    linked = tmp_path / "linked"
    linked.mkdir()
    original_is_symlink = type(tmp_path).is_symlink

    # Model an enumerated directory symlink without requiring Windows
    # Developer Mode privileges; pathlib does not descend into its pages.
    def is_symlink(path):
        return path == linked or original_is_symlink(path)

    monkeypatch.setattr(type(tmp_path), "is_symlink", is_symlink)
    assert verify_manifest(manifest, tmp_path)["status"] == "pass"
    with pytest.raises(ValueError, match="symlinked build directory"):
        verify_manifest(manifest, tmp_path, require_full_coverage=True)

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert main([str(manifest_path), str(tmp_path), "--require-full-coverage", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["status"] == "error"
    assert "symlinked build directory" in error["error"]


def test_real_symlinked_html_directory_cannot_bypass_strict_coverage(tmp_path):
    manifest = _setup(tmp_path)
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(tmp_path / "en", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Creating directory symlinks is not permitted on this host")

    with pytest.raises(ValueError, match="symlinked build directory"):
        verify_manifest(manifest, tmp_path, require_full_coverage=True)


@pytest.mark.parametrize("extension", [".html", ".HTM"])
def test_strict_coverage_rejects_linked_html_file_even_when_declared(
    tmp_path, monkeypatch, capsys, extension,
):
    manifest = _setup(tmp_path)
    linked = tmp_path / f"alias{extension}"
    linked.write_text(_page(f"{BASE}en/"), encoding="utf-8")
    manifest["pages"].append({
        "path": linked.name,
        "contract": {"url": f"{BASE}alias/", "title_present": True},
    })
    original_is_symlink = type(tmp_path).is_symlink

    # Model an in-root file symlink without relying on Windows privileges.
    def is_symlink(path):
        return path == linked or original_is_symlink(path)

    monkeypatch.setattr(type(tmp_path), "is_symlink", is_symlink)
    assert verify_manifest(manifest, tmp_path)["status"] == "pass"
    with pytest.raises(ValueError, match="symlinked HTML artifact"):
        verify_manifest(manifest, tmp_path, require_full_coverage=True)

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert main([str(manifest_path), str(tmp_path), "--require-full-coverage", "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["status"] == "error"
    assert "symlinked HTML artifact" in error["error"]


@pytest.mark.parametrize("target_exists", [True, False])
def test_real_in_root_symlinked_html_file_rejected_in_strict_coverage(
    tmp_path, target_exists,
):
    manifest = _setup(tmp_path)
    alias = tmp_path / "alias.html"
    target = tmp_path / "en" / ("index.html" if target_exists else "missing.html")
    try:
        alias.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Creating file symlinks is not permitted on this host")

    if target_exists:
        manifest["pages"].append({
            "path": "alias.html", "contract": {"url": f"{BASE}alias/", "title_present": True},
        })
    assert verify_manifest(manifest, tmp_path)["status"] == "pass"
    with pytest.raises(ValueError, match="symlinked HTML artifact"):
        verify_manifest(manifest, tmp_path, require_full_coverage=True)


@pytest.mark.parametrize("extension", [".htm", ".HTM"])
def test_manifest_can_assert_legacy_htm_artifact(tmp_path, extension):
    manifest = _setup(tmp_path)
    extra = tmp_path / f"legacy{extension}"
    extra.write_text(_page(f"{BASE}legacy/"), encoding="utf-8")
    manifest["pages"].append({
        "path": extra.name,
        "contract": {"url": f"{BASE}legacy/", "canonical": f"{BASE}legacy/"},
    })

    passing = verify_manifest(manifest, tmp_path, require_full_coverage=True)
    assert passing["status"] == "pass"
    assert passing["coverage"]["discovered"] == 3
    assert passing["coverage"]["unlisted"] == []

    extra.write_text(_page(f"{BASE}wrong/"), encoding="utf-8")
    failing = verify_manifest(manifest, tmp_path, require_full_coverage=True)
    assert failing["status"] == "fail"
    legacy_page = next(page for page in failing["pages"] if page["path"] == extra.name)
    assert [issue["code"] for issue in legacy_page["issues"]] == ["canonical"]


def test_site_cli_checks_declared_htm_routes_with_strict_coverage(tmp_path, capsys):
    manifest = _setup(tmp_path)
    extra = tmp_path / "legacy.htm"
    extra.write_text(_page(f"{BASE}legacy/"), encoding="utf-8")
    manifest["pages"].append({
        "path": extra.name,
        "contract": {"url": f"{BASE}legacy/", "title_present": True},
    })
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert main([str(path), str(tmp_path), "--require-full-coverage", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert report["coverage"]["discovered"] == 3
    assert [page["path"] for page in report["pages"]] == [
        "en/index.html", "legacy.htm", "sl/index.html",
    ]


def _case_colliding_artifact(tmp_path, monkeypatch):
    variant = tmp_path / "en" / "INDEX.HTML"
    if os.name == "nt":
        # NTFS commonly cannot store case-distinct names together. Model the
        # enumeration of a case-sensitive build without modifying host settings.
        original_rglob = type(tmp_path).rglob

        def rglob_with_variant(path, pattern):
            yield from original_rglob(path, pattern)
            if path == tmp_path.resolve() and pattern == "*":
                yield variant

        monkeypatch.setattr(type(tmp_path), "rglob", rglob_with_variant)
    else:
        variant.write_text(_page(f"{BASE}extra/"), encoding="utf-8")
    return variant


@pytest.mark.parametrize("strict", [False, True])
def test_case_colliding_html_artifact_is_unlisted(tmp_path, monkeypatch, strict):
    manifest = _setup(tmp_path)
    _case_colliding_artifact(tmp_path, monkeypatch)

    report = verify_manifest(manifest, tmp_path, require_full_coverage=strict)

    assert report["coverage"]["discovered"] == 3
    assert report["coverage"]["checked"] == 2
    assert report["coverage"]["unlisted"] == ["en/INDEX.HTML"]
    assert report["status"] == ("fail" if strict else "pass")


@pytest.mark.skipif(os.name == "nt", reason="Windows checkout lacks case-distinct files")
def test_case_distinct_manifest_entries_can_cover_both_html_artifacts(tmp_path):
    manifest = _setup(tmp_path)
    variant = tmp_path / "en" / "INDEX.HTML"
    variant.write_text(_page(f"{BASE}en/variant/"), encoding="utf-8")
    manifest["pages"].append({
        "path": "en/INDEX.HTML",
        "contract": {"url": f"{BASE}en/variant/", "canonical": f"{BASE}en/variant/"},
    })

    result = verify_manifest(manifest, tmp_path, require_full_coverage=True)
    assert result["status"] == "pass"
    assert result["coverage"] == {
        "discovered": 3, "checked": 3, "unlisted": [], "require_full_coverage": True,
    }
    assert [page["path"] for page in result["pages"]] == [
        "en/INDEX.HTML", "en/index.html", "sl/index.html",
    ]


def test_site_cli_blocks_case_colliding_artifact_with_strict_coverage(tmp_path, monkeypatch, capsys):
    manifest = _setup(tmp_path)
    _case_colliding_artifact(tmp_path, monkeypatch)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert main([str(path), str(tmp_path), "--require-full-coverage", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "fail"
    assert result["coverage"]["unlisted"] == ["en/INDEX.HTML"]


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


def test_duplicate_exact_page_paths_rejected(tmp_path):
    manifest = _setup(tmp_path)
    manifest["pages"][1]["path"] = manifest["pages"][0]["path"]
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


@pytest.mark.parametrize("second_url", [
    "https://EXAMPLE.com/same/",
    "https://example.COM/same/",
    "HTTPS://ExAmPlE.COM/same/",
])
def test_manifest_rejects_duplicate_urls_with_hostname_case_variants(tmp_path, second_url):
    manifest = _setup(tmp_path)
    for entry, url in zip(
        manifest["pages"], ("https://example.com/same/", second_url), strict=True
    ):
        entry["contract"] = {"url": url, "title_present": True}
    with pytest.raises(ValueError, match="Duplicate contract URL"):
        verify_manifest(manifest, tmp_path)


def test_site_cli_rejects_hostname_case_duplicate_as_input_error(tmp_path, capsys):
    manifest = _setup(tmp_path)
    for entry, url in zip(
        manifest["pages"], ("https://example.com/same/", "https://EXAMPLE.com/same/"),
        strict=True,
    ):
        entry["contract"] = {"url": url, "title_present": True}
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    assert main([str(manifest_file), str(tmp_path), "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert "Duplicate contract URL" in result["error"]


@pytest.mark.parametrize("first_url,second_url", [
    ("https://example.com/same/", "https://example.com:443/same/"),
    ("http://example.com/same/", "http://example.com:80/same/"),
    ("https://example.com/", "https://example.com"),
    ("http://example.com", "http://example.com:80/"),
    ("https://[::1]/", "https://[::1]:443/"),
])
def test_manifest_rejects_duplicate_urls_with_default_ports_or_empty_root(
    tmp_path, first_url, second_url
):
    manifest = _setup(tmp_path)
    for entry, url in zip(manifest["pages"], (first_url, second_url), strict=True):
        entry["contract"] = {"url": url, "title_present": True}
    with pytest.raises(ValueError, match="Duplicate contract URL"):
        verify_manifest(manifest, tmp_path)


def test_site_cli_rejects_default_port_duplicate_as_input_error(tmp_path, capsys):
    manifest = _setup(tmp_path)
    for entry, url in zip(
        manifest["pages"], ("https://example.com/", "https://example.com:443/"),
        strict=True,
    ):
        entry["contract"] = {"url": url, "title_present": True}
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    assert main([str(manifest_file), str(tmp_path), "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert "Duplicate contract URL" in result["error"]


@pytest.mark.parametrize("second_url", [
    "https://example.com:8443/same/",
    "http://example.com:443/same/",
    "https://example.com/same/?q=one",
    "https://example.com/Same/",
])
def test_manifest_retains_distinct_ports_schemes_paths_and_queries(tmp_path, second_url):
    manifest = _setup(tmp_path)
    for entry, url in zip(
        manifest["pages"], ("https://example.com/same/", second_url), strict=True
    ):
        entry["contract"] = {"url": url, "title_present": True}
    assert verify_manifest(manifest, tmp_path)["status"] == "pass"


@pytest.mark.parametrize("second_url", [
    "https://EXAMPLE.com/Same/?q=one",
    "https://EXAMPLE.com/same/?q=ONE",
    "http://EXAMPLE.com/same/?q=one",
    "https://EXAMPLE.com:8443/same/?q=one",
])
def test_manifest_preserves_path_and_query_case_distinctions(tmp_path, second_url):
    manifest = _setup(tmp_path)
    for entry, url in zip(
        manifest["pages"], ("https://example.com/same/?q=one", second_url),
        strict=True,
    ):
        entry["contract"] = {"url": url, "title_present": True}
    assert verify_manifest(manifest, tmp_path)["status"] == "pass"


def test_manifest_cannot_treat_fragment_variants_as_distinct_search_pages(tmp_path, capsys):
    manifest = _setup(tmp_path)
    for entry, fragment in zip(manifest["pages"], ("#one", "#two"), strict=True):
        entry["contract"] = {"url": f"{BASE}landing/{fragment}", "title_present": True}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert main([str(path), str(tmp_path), "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["status"] == "error"
    assert "'url'" in error["error"] and "fragment" in error["error"]


@pytest.mark.parametrize("bad_url", [
    "https://example.com:invalid/sl/",
    "https://ex%61mple.com/sl/",
])
def test_site_cli_rejects_invalid_url_before_cross_page_checks(tmp_path, capsys, bad_url):
    manifest = _setup(tmp_path)
    manifest["pages"][0]["contract"]["url"] = bad_url
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


@pytest.mark.parametrize("machine_readable", [False, True])
@pytest.mark.parametrize("container", ["array", "object"])
def test_deep_manifest_json_returns_input_error_not_traceback(
    tmp_path, capsys, machine_readable, container
):
    path = tmp_path / "manifest.json"
    nested = (
        "[" * 6000 + "0" + "]" * 6000
        if container == "array"
        else '{"a":' * 6000 + "0" + "}" * 6000
    )
    path.write_text(nested, encoding="utf-8")
    args = [str(path), str(tmp_path)]
    if machine_readable:
        args.append("--json")

    assert main(args) == 2
    output = capsys.readouterr()
    if machine_readable:
        result = json.loads(output.out)
        assert result["status"] == "error"
        # Python runtimes can reject nesting in the decoder or at schema
        # validation; neither path may escape as an uncaught exception.
        assert result["error"].startswith("Manifest ")
        assert output.err == ""
    else:
        assert output.out == ""
        assert "Invalid input: Manifest " in output.err


def test_declared_url_never_fetched(tmp_path, monkeypatch):
    manifest = _setup(tmp_path)

    def blocked_network(*_args, **_kwargs):
        raise AssertionError("Network use forbidden for offline contracts")

    monkeypatch.setattr(socket, "create_connection", blocked_network)
    assert verify_manifest(manifest, tmp_path)["status"] == "pass"


def test_site_cli_human_output_escapes_real_hreflang_issue_code(tmp_path, capsys):
    manifest = _setup(tmp_path)
    artifact = tmp_path / "en" / "index.html"
    artifact.write_text(
        _page(f"{BASE}en/").replace(
            "</head>",
            '<link rel="alternate" hreflang="de\nFORGED LOG" href="javascript:bad"></head>',
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert main([str(manifest_path), str(tmp_path), "--check-hreflang-reciprocity"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert all(not line.startswith("forged log") for line in lines)
    assert any("hreflang_invalid_url:de\\nforged log" in line for line in lines)


def test_site_cli_human_output_bounds_issues_and_unlisted_html(tmp_path, capsys):
    manifest = _setup(tmp_path)
    manifest["pages"][0]["contract"]["title_equals"] = "E" * 1600
    for i in range(35):
        (tmp_path / f"unlisted-{i:02}.html").write_text("<html></html>", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    args = [str(manifest_path), str(tmp_path), "--require-full-coverage"]

    assert main(args) == 1
    lines = capsys.readouterr().out.splitlines()
    assert max(map(len, lines)) < 500
    assert "... and 15 more" in "\n".join(lines)
    assert "35 unlisted" in lines[0]
    assert main(args + ["--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert len(result["coverage"]["unlisted"]) == 35
    assert result["pages"][-1]["issues"][0]["expected"] == "E" * 1600
