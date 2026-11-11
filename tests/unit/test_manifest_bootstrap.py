"""Bootstrap a useful offline SEO manifest from local build artifacts only."""

import json
import socket
from pathlib import Path

import pytest

from searcharis.manifest_bootstrap import generate_manifest, main
from searcharis.site_ci import verify_manifest


def _html(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<!doctype html><html><head><title>Example</title></head><body></body></html>",
        encoding="utf-8",
    )


def test_bootstrap_is_deterministic_and_usable_with_strict_site_ci(tmp_path):
    build = tmp_path / "dist"
    for name in ("sl/index.html", "en/index.html", "index.html", "legal/terms.htm"):
        _html(build / name)
    (build / "robots.txt").write_text("User-agent: *\n", encoding="utf-8")

    first = generate_manifest(build, "https://example.com/docs/")
    assert first == generate_manifest(build, "https://example.com/docs/")
    assert first == {
        "version": 1,
        "pages": [
            {"path": "en/index.html", "contract": {
                "url": "https://example.com/docs/en/", "title_present": True,
            }},
            {"path": "index.html", "contract": {
                "url": "https://example.com/docs/", "title_present": True,
            }},
            {"path": "legal/terms.htm", "contract": {
                "url": "https://example.com/docs/legal/terms.htm", "title_present": True,
            }},
            {"path": "sl/index.html", "contract": {
                "url": "https://example.com/docs/sl/", "title_present": True,
            }},
        ],
    }
    report = verify_manifest(first, build, require_full_coverage=True)
    assert report["status"] == "pass"
    assert report["coverage"]["discovered"] == 4


def test_cli_outputs_complete_json_and_never_opens_a_socket(tmp_path, capsys, monkeypatch):
    build = tmp_path / "build"
    _html(build / "index.html")
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))
    assert main([str(build), "https://example.com"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["pages"][0]["contract"]["url"] == "https://example.com/"
    assert verify_manifest(result, build, require_full_coverage=True)["status"] == "pass"


def test_cli_output_file_is_utf8_and_never_overwritten(tmp_path, capsys):
    build = tmp_path / "dist"
    _html(build / "index.html")
    destination = tmp_path / "manifest.json"
    args = [str(build), "https://example.com/", "--output", str(destination)]
    assert main(args) == 0
    assert capsys.readouterr().out == ""
    payload = destination.read_bytes()
    assert payload.startswith(b"{")
    assert json.loads(payload)["pages"][0]["path"] == "index.html"
    assert main(args) == 2
    assert destination.read_bytes() == payload
    assert "Invalid input:" in capsys.readouterr().err


def test_opt_in_check_built_returns_mismatch_but_prints_reviewable_manifest(tmp_path, capsys):
    build = tmp_path / "dist"
    _html(build / "index.html")
    (build / "index.html").write_text("<html><head></head><body></body></html>")
    assert main([str(build), "https://example.com/", "--check-built"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["pages"][0]["path"] == "index.html"
    assert "FAIL 0/1" in captured.err
    assert "title_presence" in captured.err
    # Without the opt-in, generation alone is not an assertion check.
    assert main([str(build), "https://example.com/"]) == 0


def test_opt_in_check_built_accepts_valid_pages_without_network(tmp_path, capsys, monkeypatch):
    build = tmp_path / "dist"
    _html(build / "index.html")
    target = tmp_path / "seo-manifest.json"
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))
    assert main([
        str(build), "https://example.com/", "--check-built", "--output", str(target),
    ]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "PASS 1/1" in captured.err
    assert verify_manifest(json.loads(target.read_text()), build)["status"] == "pass"


def test_opt_in_check_built_keeps_reviewable_manifest_on_failed_assertion(tmp_path, capsys):
    build = tmp_path / "dist"
    _html(build / "index.html")
    target = tmp_path / "seo-manifest.json"
    assert main([
        str(build), "https://example.com/", "--expect-self-canonical",
        "--check-built", "--output", str(target),
    ]) == 1
    assert json.loads(target.read_text())["pages"][0]["contract"]["canonical"] == (
        "https://example.com/"
    )
    assert "canonical" in capsys.readouterr().err
    # Create-only protection is unchanged even for a failed optional check.
    original = target.read_bytes()
    assert main([str(build), "https://example.com/", "--output", str(target)]) == 2
    assert target.read_bytes() == original


def test_opt_in_check_built_input_errors_do_not_create_output(tmp_path, capsys):
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_bytes(b"\xff")
    target = tmp_path / "seo-manifest.json"
    assert main([
        str(build), "https://example.com/", "--check-built", "--output", str(target),
    ]) == 2
    assert not target.exists()
    assert "Invalid input:" in capsys.readouterr().err


def test_starter_title_policy_fails_when_built_title_is_missing(tmp_path):
    _html(tmp_path / "index.html")
    (tmp_path / "index.html").write_text("<html><head></head><body></body></html>")
    manifest = generate_manifest(tmp_path, "https://example.com/")
    report = verify_manifest(manifest, tmp_path, require_full_coverage=True)
    assert report["status"] == "fail"
    assert report["pages"][0]["issues"][0]["code"] == "title_presence"


def test_opt_in_self_canonical_populates_explicit_expected_urls(tmp_path):
    build = tmp_path / "dist"
    _html(build / "index.html")
    _html(build / "en" / "index.html")
    _html(build / "about us.html")

    basic = generate_manifest(build, "https://example.com/docs/")
    assert all("canonical" not in page["contract"] for page in basic["pages"])

    manifest = generate_manifest(
        build, "https://example.com/docs/", expect_self_canonical=True,
    )
    assert [page["contract"]["canonical"] for page in manifest["pages"]] == [
        "https://example.com/docs/about%20us.html",
        "https://example.com/docs/en/",
        "https://example.com/docs/",
    ]
    assert all(
        page["contract"]["canonical"] == page["contract"]["url"]
        for page in manifest["pages"]
    )


def test_opt_in_self_canonical_detects_missing_or_changed_built_link(tmp_path):
    html = tmp_path / "index.html"
    _html(html)
    manifest = generate_manifest(
        tmp_path, "https://example.com/", expect_self_canonical=True,
    )
    assert verify_manifest(manifest, tmp_path)["status"] == "fail"

    html.write_text(
        '<html><head><title>Example</title>'
        '<link rel="canonical" href="https://example.com/"></head></html>',
        encoding="utf-8",
    )
    assert verify_manifest(manifest, tmp_path)["status"] == "pass"

    html.write_text(
        '<html><head><title>Example</title>'
        '<link rel="canonical" href="https://example.com/other/"></head></html>',
        encoding="utf-8",
    )
    assert verify_manifest(manifest, tmp_path)["status"] == "fail"


def test_cli_opt_in_self_canonical_is_json_and_offline(tmp_path, monkeypatch, capsys):
    _html(tmp_path / "index.html")
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))
    assert main([str(tmp_path), "https://example.com/", "--expect-self-canonical"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["pages"][0]["contract"] == {
        "url": "https://example.com/",
        "title_present": True,
        "canonical": "https://example.com/",
    }


def test_opt_in_indexability_guard_is_explicit_and_composes_with_canonical(tmp_path):
    _html(tmp_path / "index.html")
    default = generate_manifest(tmp_path, "https://example.com/")
    assert "indexable" not in default["pages"][0]["contract"]

    manifest = generate_manifest(
        tmp_path, "https://example.com/",
        expect_indexable=True,
        expect_self_canonical=True,
    )
    assert manifest["pages"][0]["contract"] == {
        "url": "https://example.com/",
        "title_present": True,
        "canonical": "https://example.com/",
        "indexable": True,
    }


@pytest.mark.parametrize("placement", ["head", "body"])
@pytest.mark.parametrize("bot", ["robots", "googlebot"])
def test_opt_in_indexability_guard_detects_noindex_in_built_html(
    tmp_path, placement, bot,
):
    html = tmp_path / "index.html"
    _html(html)
    manifest = generate_manifest(
        tmp_path, "https://example.com/", expect_indexable=True,
    )
    assert verify_manifest(manifest, tmp_path)["status"] == "pass"

    robots = f'<meta name="{bot}" content="noindex,follow">'
    if placement == "head":
        html.write_text(f"<html><head><title>Example</title>{robots}</head></html>")
    else:
        html.write_text(f"<html><head><title>Example</title></head><body>{robots}</body></html>")
    assert verify_manifest(manifest, tmp_path)["status"] == "fail"

    default = generate_manifest(tmp_path, "https://example.com/")
    assert verify_manifest(default, tmp_path)["status"] == "pass"


def test_cli_opt_in_indexability_guard_is_json_and_offline(tmp_path, monkeypatch, capsys):
    _html(tmp_path / "index.html")
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))
    assert main([str(tmp_path), "https://example.com/", "--expect-indexable"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["pages"][0]["contract"]["indexable"] is True


def test_opt_in_description_presence_is_explicit_and_composable(tmp_path):
    _html(tmp_path / "index.html")
    basic = generate_manifest(tmp_path, "https://example.com/")
    assert "description_present" not in basic["pages"][0]["contract"]

    manifest = generate_manifest(
        tmp_path, "https://example.com/", expect_description=True,
        expect_self_canonical=True, expect_indexable=True,
    )
    assert manifest["pages"][0]["contract"] == {
        "url": "https://example.com/",
        "title_present": True,
        "canonical": "https://example.com/",
        "indexable": True,
        "description_present": True,
    }


@pytest.mark.parametrize("description", [None, "", "  "])
def test_opt_in_description_detects_missing_or_blank_head_meta(tmp_path, description):
    page = tmp_path / "index.html"
    _html(page)
    manifest = generate_manifest(tmp_path, "https://example.com/", expect_description=True)
    meta = "" if description is None else f'<meta name="description" content="{description}">'
    page.write_text(f"<html><head><title>Example</title>{meta}</head></html>")
    report = verify_manifest(manifest, tmp_path)
    assert report["status"] == "fail"
    assert report["pages"][0]["issues"][0]["code"] == "description_presence"

    page.write_text(
        '<html><head><title>Example</title><meta name="description" '
        'content="A useful page summary"></head></html>'
    )
    assert verify_manifest(manifest, tmp_path)["status"] == "pass"


def test_cli_opt_in_description_is_json_and_offline(tmp_path, monkeypatch, capsys):
    _html(tmp_path / "index.html")
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))
    assert main([str(tmp_path), "https://example.com/", "--expect-description"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["pages"][0]["contract"]["description_present"] is True


def test_skip_opt_ins_for_reviewed_exception_keeps_full_coverage(tmp_path):
    build = tmp_path / "dist"
    _html(build / "index.html")
    (build / "index.html").write_text(
        '<html><head><title>Home</title><meta name="description" content="Home">'
        '<link rel="canonical" href="https://example.com/"></head></html>',
        encoding="utf-8",
    )
    _html(build / "404.html")
    (build / "404.html").write_text(
        '<html><head><title>Not found</title><meta name="robots" content="noindex">'
        '</head></html>', encoding="utf-8",
    )
    options = {
        "expect_self_canonical": True,
        "expect_indexable": True,
        "expect_description": True,
    }
    unreviewed = generate_manifest(build, "https://example.com/", **options)
    assert verify_manifest(unreviewed, build, require_full_coverage=True)["status"] == "fail"

    reviewed = generate_manifest(
        build, "https://example.com/", skip_opt_ins_for=("404.html",), **options,
    )
    assert [page["path"] for page in reviewed["pages"]] == ["404.html", "index.html"]
    assert reviewed["pages"][0]["contract"] == {
        "url": "https://example.com/404.html", "title_present": True,
    }
    assert reviewed["pages"][1]["contract"]["indexable"] is True
    result = verify_manifest(reviewed, build, require_full_coverage=True)
    assert result["status"] == "pass"
    assert result["coverage"]["discovered"] == 2
    assert result["coverage"]["unlisted"] == []

    (build / "index.html").write_text(
        '<html><head><title>Home</title><meta name="robots" content="noindex">'
        '<meta name="description" content="Home">'
        '<link rel="canonical" href="https://example.com/"></head></html>',
        encoding="utf-8",
    )
    assert verify_manifest(reviewed, build, require_full_coverage=True)["status"] == "fail"


@pytest.mark.parametrize("path", ["../404.html", "missing.html", "404.HTML", "404.html/../404.html"])
def test_skip_opt_ins_for_rejects_unsafe_or_unknown_exact_paths(tmp_path, path):
    _html(tmp_path / "404.html")
    with pytest.raises(ValueError, match="skip|Unsafe|unknown"):
        generate_manifest(
            tmp_path, "https://example.com/", expect_indexable=True,
            skip_opt_ins_for=(path,),
        )


def test_skip_opt_ins_for_rejects_duplicates_and_no_optional_assertions(tmp_path):
    _html(tmp_path / "404.html")
    with pytest.raises(ValueError, match="duplicate"):
        generate_manifest(
            tmp_path, "https://example.com/", expect_indexable=True,
            skip_opt_ins_for=("404.html", "404.html"),
        )
    with pytest.raises(ValueError, match="optional"):
        generate_manifest(tmp_path, "https://example.com/", skip_opt_ins_for=("404.html",))


def test_cli_skip_opt_ins_for_is_explicit_exact_path_and_offline(tmp_path, capsys, monkeypatch):
    _html(tmp_path / "index.html")
    _html(tmp_path / "404.html")
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))
    args = [str(tmp_path), "https://example.com/", "--expect-indexable",
            "--skip-opt-ins-for", "404.html"]
    assert main(args) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert "indexable" not in manifest["pages"][0]["contract"]
    assert manifest["pages"][1]["contract"]["indexable"] is True
    assert main(args + ["--skip-opt-ins-for", "missing.html"]) == 2
    assert "Invalid input:" in capsys.readouterr().err


def test_reviewed_noindex_paths_enforce_negative_policy_without_dropping_coverage(tmp_path):
    build = tmp_path / "dist"
    _html(build / "index.html")
    _html(build / "404.html")
    (build / "404.html").write_text(
        '<html><head><title>Not found</title>'
        '<meta name="robots" content="noindex"></head></html>',
        encoding="utf-8",
    )
    options = {
        "expect_indexable": True,
        "expect_description": True,
        "skip_opt_ins_for": ("404.html",),
        "expect_noindex_for": ("404.html",),
    }
    (build / "index.html").write_text(
        '<html><head><title>Home</title>'
        '<meta name="description" content="Home"></head></html>',
        encoding="utf-8",
    )
    manifest = generate_manifest(build, "https://example.com/", **options)
    assert manifest["pages"][0]["contract"] == {
        "url": "https://example.com/404.html",
        "title_present": True,
        "indexable": False,
    }
    assert manifest["pages"][1]["contract"]["indexable"] is True
    assert verify_manifest(manifest, build, require_full_coverage=True)["status"] == "pass"

    _html(build / "404.html")
    assert verify_manifest(manifest, build, require_full_coverage=True)["status"] == "fail"
    (build / "404.html").write_text(
        '<html><head><title>Not found</title>'
        '<meta name="robots" content="noindex"></head></html>',
        encoding="utf-8",
    )
    (build / "index.html").write_text(
        '<html><head><title>Home</title><meta name="robots" content="noindex">'
        '<meta name="description" content="Home"></head></html>',
        encoding="utf-8",
    )
    assert verify_manifest(manifest, build, require_full_coverage=True)["status"] == "fail"


@pytest.mark.parametrize("path", [
    "../404.html", "missing.html", "404.HTML", "404.html/../404.html",
])
def test_reviewed_noindex_paths_reject_unsafe_or_unknown_entries(tmp_path, path):
    _html(tmp_path / "404.html")
    with pytest.raises(ValueError, match="noindex|Unsafe|unknown"):
        generate_manifest(
            tmp_path, "https://example.com/", expect_noindex_for=(path,),
        )


def test_reviewed_noindex_paths_reject_duplicates(tmp_path):
    _html(tmp_path / "404.html")
    with pytest.raises(ValueError, match="duplicate"):
        generate_manifest(
            tmp_path, "https://example.com/",
            expect_noindex_for=("404.html", "404.html"),
        )


def test_cli_reviewed_noindex_is_offline_and_fails_closed(tmp_path, monkeypatch, capsys):
    _html(tmp_path / "404.html")
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))
    args = [str(tmp_path), "https://example.com/", "--expect-noindex-for", "404.html"]
    assert main(args) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert manifest["pages"][0]["contract"]["indexable"] is False
    assert verify_manifest(manifest, tmp_path)["status"] == "fail"
    assert main(args + ["--expect-noindex-for", "404.html"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid input:" in captured.err


def test_reviewed_route_overrides_support_host_rewrites_without_changing_coverage(tmp_path):
    build = tmp_path / "dist"
    _html(build / "index.html")
    _html(build / "about.html")
    (build / "about.html").write_text(
        '<html><head><title>About</title>'
        '<link rel="canonical" href="https://example.com/docs/about/">'
        '</head></html>', encoding="utf-8",
    )
    manifest = generate_manifest(
        build, "https://example.com/docs/", expect_self_canonical=True,
        route_overrides={"about.html": "about/"},
        skip_opt_ins_for=("index.html",),
    )
    assert [page["contract"]["url"] for page in manifest["pages"]] == [
        "https://example.com/docs/about/", "https://example.com/docs/",
    ]
    assert manifest["pages"][0]["contract"]["canonical"] == (
        "https://example.com/docs/about/"
    )
    assert verify_manifest(manifest, build, require_full_coverage=True)["status"] == "pass"


@pytest.mark.parametrize("route", [
    "/other/", "//evil.test/path", "../escape/", "a/../b/", "a/./b/",
    "a//b/", "https://evil.test/", "a?b", "a#b", "a\\b", "a%2fb",
    ".", "..", "a//", " ", "a\nb", 123,
])
def test_reviewed_route_overrides_reject_unsafe_values(tmp_path, route):
    _html(tmp_path / "index.html")
    with pytest.raises(ValueError, match="route|unsafe"):
        generate_manifest(
            tmp_path, "https://example.com/", route_overrides={"index.html": route},
        )


@pytest.mark.parametrize("path", ["missing.html", "../index.html", "Index.html"])
def test_reviewed_route_overrides_reject_unlisted_or_unsafe_page(tmp_path, path):
    _html(tmp_path / "index.html")
    with pytest.raises(ValueError, match="route|Unsafe|unknown"):
        generate_manifest(
            tmp_path, "https://example.com/", route_overrides={path: "home/"},
        )


def test_reviewed_route_overrides_detect_collisions_with_inferred_urls(tmp_path):
    _html(tmp_path / "a.html")
    _html(tmp_path / "b.html")
    with pytest.raises(ValueError, match="same URL"):
        generate_manifest(
            tmp_path, "https://example.com/", route_overrides={"a.html": "b.html"},
        )


def test_reviewed_route_overrides_encode_unescaped_paths_under_public_prefix(tmp_path):
    _html(tmp_path / "index.html")
    result = generate_manifest(
        tmp_path, "https://example.com/docs/",
        route_overrides={"index.html": "about us/"},
    )
    assert result["pages"][0]["contract"]["url"] == (
        "https://example.com/docs/about%20us/"
    )
    assert generate_manifest(tmp_path, "https://example.com/docs/")["pages"][0][
        "contract"
    ]["url"] == "https://example.com/docs/"


def test_cli_reviewed_route_map_json_is_offline_and_fail_closed(tmp_path, capsys, monkeypatch):
    build = tmp_path / "dist"
    _html(build / "about.html")
    route_map = tmp_path / "routes.json"
    route_map.write_text(
        '{"version":1,"routes":{"about.html":"about/"}}', encoding="utf-8",
    )
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))
    args = [str(build), "https://example.com/docs/", "--route-map", str(route_map)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["pages"][0]["contract"]["url"] == (
        "https://example.com/docs/about/"
    )
    for bad in (
        '{"version":1,"routes":{"about.html":"about/","about.html":"alt/"}}',
        '{"version":true,"routes":{"about.html":"about/"}}',
        '{"version":1,"routes":{"unknown.html":"about/"}}',
        '{"version":1,"routes":{}}',
        '{"version":1,"routes":{"about.html":"https://evil.test/"}}',
        '{',
    ):
        route_map.write_text(bad, encoding="utf-8")
        assert main(args) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Invalid input:" in captured.err


def test_invalid_route_map_never_creates_or_overwrites_output(tmp_path, capsys):
    build = tmp_path / "dist"
    _html(build / "index.html")
    route_map = tmp_path / "routes.json"
    route_map.write_text('{"version":1,"routes":{"nope.html":"a/"}}', encoding="utf-8")
    destination = tmp_path / "manifest.json"
    args = [str(build), "https://example.com/", "--route-map", str(route_map),
            "--output", str(destination)]
    assert main(args) == 2
    assert not destination.exists()
    destination.write_text("original", encoding="utf-8")
    route_map.write_text('{"version":1,"routes":{"index.html":"home/"}}',
                         encoding="utf-8")
    assert main(args) == 2
    assert destination.read_text(encoding="utf-8") == "original"
    assert "Invalid input:" in capsys.readouterr().err


@pytest.mark.parametrize("generator,path,route", [
    ("astro", "en/index.html", "english/"),
    ("vite", "pricing/index.html", "plans/"),
])
def test_reviewed_route_map_preserves_full_static_fixture_coverage(generator, path, route):
    build = (
        Path(__file__).resolve().parents[2]
        / "examples/seo-contract/generator-fixtures"
        / generator / "artifacts"
    )
    manifest = generate_manifest(
        build, "https://fixtures.invalid/", route_overrides={path: route},
    )
    assert next(page for page in manifest["pages"] if page["path"] == path)[
        "contract"
    ]["url"] == f"https://fixtures.invalid/{route}"
    result = verify_manifest(manifest, build, require_full_coverage=True)
    assert result["status"] == "pass"
    assert result["summary"]["checked"] == 2
    assert result["coverage"]["unlisted"] == []


@pytest.mark.parametrize("generator", ["astro", "vite"])
def test_opt_in_indexability_guard_passes_static_generator_artifacts(generator):
    build = (
        Path(__file__).resolve().parents[2]
        / "examples/seo-contract/generator-fixtures"
        / generator
        / "artifacts"
    )
    manifest = generate_manifest(
        build, "https://fixtures.invalid/", expect_indexable=True,
    )
    report = verify_manifest(manifest, build, require_full_coverage=True)
    assert report["status"] == "pass"
    assert report["summary"]["checked"] == 2
    assert report["coverage"]["unlisted"] == []


@pytest.mark.parametrize("generator", ["astro", "vite"])
def test_opt_in_self_canonical_passes_static_generator_artifacts(generator):
    build = (
        Path(__file__).resolve().parents[2]
        / "examples/seo-contract/generator-fixtures"
        / generator
        / "artifacts"
    )
    manifest = generate_manifest(
        build, "https://fixtures.invalid/", expect_self_canonical=True,
    )
    report = verify_manifest(manifest, build, require_full_coverage=True)
    assert report["status"] == "pass"
    assert report["summary"]["checked"] == 2
    assert report["coverage"]["unlisted"] == []


def test_bootstrap_escapes_route_characters_without_leaking_urls(tmp_path):
    build = tmp_path / "dist"
    _html(build / "search #1" / "index.html")
    _html(build / "about us.HTML")
    result = generate_manifest(build, "https://example.com/")
    assert [page["contract"]["url"] for page in result["pages"]] == [
        "https://example.com/about%20us.HTML",
        "https://example.com/search%20%231/",
    ]


@pytest.mark.parametrize("base", [
    "http://", "file:///tmp/", "https://user:secret@example.com/",
    "https://example.com/docs", "https://example.com/?q=1",
    "https://example.com/#fragment", "https://example.com/?",
    "https://example.com/#", "https://example.com\\path/",
    "https://example.com:99999/",
    "https://example.com/docs/../", "https://example.com/docs/./",
    "https://example.com/docs/%2e%2e/", "https://example.com/docs/.%2E/",
    "https://example.com/%2e/docs/", "https://example.com/docs/%2e./",
    "https://example.com/docs//", "https://example.com/docs///",
    "https://example.com//docs/", "https://example.com//",
    "https://example.com/a//b/",
])
def test_invalid_or_ambiguous_base_urls_fail_closed(tmp_path, base):
    _html(tmp_path / "index.html")
    with pytest.raises(ValueError, match="base URL"):
        generate_manifest(tmp_path, base)


def test_encoded_non_dot_public_base_path_still_preserves_prefix(tmp_path):
    _html(tmp_path / "index.html")
    _html(tmp_path / "about.html")
    result = generate_manifest(tmp_path, "https://example.com/my%20docs/")
    assert [page["contract"]["url"] for page in result["pages"]] == [
        "https://example.com/my%20docs/about.html",
        "https://example.com/my%20docs/",
    ]


def test_dot_segment_base_cli_fails_without_creating_manifest(tmp_path, capsys):
    _html(tmp_path / "index.html")
    target = tmp_path / "manifest.json"
    result = main([
        str(tmp_path), "https://example.com/docs/../", "--output", str(target),
    ])
    assert result == 2
    assert not target.exists()
    assert capsys.readouterr().out == ""


def test_repeated_slash_base_cli_fails_without_creating_manifest(tmp_path, capsys):
    _html(tmp_path / "index.html")
    _html(tmp_path / "about.html")
    target = tmp_path / "manifest.json"
    result = main([
        str(tmp_path), "https://example.com/docs//", "--output", str(target),
    ])
    assert result == 2
    assert not target.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid input:" in captured.err


def test_cli_rejects_invalid_base_with_input_error_and_no_json(tmp_path, capsys):
    _html(tmp_path / "index.html")
    assert main([str(tmp_path), "https://example.com/path"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Invalid input:" in captured.err


def test_empty_build_rejected_without_generating_empty_manifest(tmp_path):
    with pytest.raises(ValueError, match="No HTML"):
        generate_manifest(tmp_path, "https://example.com/")


def test_duplicate_route_mappings_are_rejected(tmp_path):
    _html(tmp_path / "a" / "index.html")
    _html(tmp_path / "a" / "index.htm")
    with pytest.raises(ValueError, match="same URL"):
        generate_manifest(tmp_path, "https://example.com/")


def test_maximum_manifest_pages_is_enforced(tmp_path, monkeypatch):
    import searcharis.manifest_bootstrap as bootstrap

    _html(tmp_path / "a.html")
    _html(tmp_path / "b.html")
    monkeypatch.setattr(bootstrap, "_MAX_PAGES", 1)
    with pytest.raises(ValueError, match="more than 1"):
        generate_manifest(tmp_path, "https://example.com/")


def test_symlinked_html_is_rejected_when_host_allows_links(tmp_path):
    build = tmp_path / "dist"
    _html(build / "index.html")
    alias = build / "alias.html"
    try:
        alias.symlink_to(build / "index.html")
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation unavailable on this Windows host")
    with pytest.raises(ValueError, match="Symlink"):
        generate_manifest(build, "https://example.com/")
