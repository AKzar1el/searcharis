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


@pytest.mark.parametrize(
    "output_relative",
    ("seo-manifest.json", "new-page.html", "reports/seo-manifest.json", "reports/../alias.html"),
)
@pytest.mark.parametrize("check_built", (False, True))
def test_cli_rejects_output_inside_immutable_build(tmp_path, capsys, output_relative, check_built):
    build = tmp_path / "dist"
    _html(build / "index.html")
    (build / "reports").mkdir()
    destination = build / output_relative
    original = (build / "index.html").read_bytes()

    args = [str(build), "https://example.com/", "--output", str(destination)]
    if check_built:
        args.append("--check-built")
    assert main(args) == 2
    assert not destination.exists()
    assert (build / "index.html").read_bytes() == original
    assert "outside the build" in capsys.readouterr().err


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


def test_selected_pages_create_reviewable_incremental_manifest(tmp_path, capsys, monkeypatch):
    build = tmp_path / "dist"
    for path in ("index.html", "pricing/index.html", "legal/index.html"):
        _html(build / path)
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))
    destination = tmp_path / "reviewed.json"
    assert main([
        str(build), "https://example.com/", "--only", "pricing/index.html",
        "--only", "index.html", "--check-built", "--output", str(destination),
    ]) == 0
    assert "PASS 2/2" in capsys.readouterr().err
    manifest = json.loads(destination.read_text(encoding="utf-8"))
    assert [page["path"] for page in manifest["pages"]] == [
        "index.html", "pricing/index.html",
    ]
    report = verify_manifest(manifest, build)
    assert report["status"] == "pass"
    assert report["coverage"]["unlisted"] == ["legal/index.html"]
    assert verify_manifest(manifest, build, require_full_coverage=True)["status"] == "fail"


def test_curated_page_file_and_explicit_only_create_exact_reviewed_subset(
    tmp_path, capsys, monkeypatch,
):
    build = tmp_path / "dist"
    for path in ("index.html", "pricing/index.html", "legal/index.html", "blog/index.html"):
        _html(build / path)
    selected = tmp_path / "curated-paths.txt"
    selected.write_text(
        "\ufeff# Reviewed landing pages\npricing/index.html\n\nlegal/index.html\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))

    assert main([
        str(build), "https://example.com/", "--only-file", str(selected),
        "--only", "index.html", "--check-built",
    ]) == 0
    captured = capsys.readouterr()
    manifest = json.loads(captured.out)
    assert "PASS 3/3" in captured.err
    assert [page["path"] for page in manifest["pages"]] == [
        "index.html", "legal/index.html", "pricing/index.html",
    ]
    assert verify_manifest(manifest, build)["coverage"]["unlisted"] == ["blog/index.html"]


def test_curated_page_file_accepts_indented_comments_and_whitespace_blank_lines(
    tmp_path, capsys, monkeypatch,
):
    build = tmp_path / "dist"
    _html(build / "index.html")
    _html(build / "pricing/index.html")
    selected = tmp_path / "manual-review.txt"
    selected.write_bytes(
        b"# reviewed paths\r\n  \t  \r\n  # keep this comment\r\npricing/index.html\r\n"
    )
    destination = tmp_path / "new-review.json"
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))

    assert main([
        str(build), "https://example.com/", "--only-file", str(selected),
        "--check-built", "--output", str(destination),
    ]) == 0
    assert "PASS 1/1" in capsys.readouterr().err
    manifest = json.loads(destination.read_text(encoding="utf-8"))
    assert [item["path"] for item in manifest["pages"]] == ["pricing/index.html"]


@pytest.mark.parametrize("separator", ["\v", "\f", "\x85", "\u2028", "\u2029"])
def test_curated_page_file_does_not_promote_unicode_separators_to_new_reviews(
    tmp_path, capsys, separator,
):
    build = tmp_path / "dist"
    _html(build / "index.html")
    _html(build / "pricing/index.html")
    selected = tmp_path / "manual-review.txt"
    selected.write_text(f"index.html{separator}pricing/index.html\n", encoding="utf-8")
    destination = tmp_path / "reviewed.json"

    assert main([
        str(build), "https://example.com/", "--only-file", str(selected),
        "--output", str(destination),
    ]) == 2
    assert not destination.exists()
    assert "Invalid input:" in capsys.readouterr().err


@pytest.mark.parametrize("selection_text", [
    "# comments only\n\n",
    "index.html\nindex.html\n",
    "index.html\nmissing.html\n",
    "../outside.html\n",
    "index.html \n",
])
def test_curated_page_file_rejects_empty_duplicate_or_unsafe_selections(
    tmp_path, capsys, selection_text,
):
    build = tmp_path / "dist"
    _html(build / "index.html")
    selection = tmp_path / "curated.txt"
    selection.write_text(selection_text, encoding="utf-8")
    output = tmp_path / "manifest.json"

    assert main([
        str(build), "https://example.com/", "--only-file", str(selection),
        "--output", str(output),
    ]) == 2
    assert not output.exists()
    assert "Invalid input:" in capsys.readouterr().err


def test_curated_page_file_extend_preserves_previous_review(tmp_path, capsys):
    build = tmp_path / "dist"
    _html(build / "index.html")
    _html(build / "pricing/index.html")
    existing = tmp_path / "old.json"
    prior = generate_manifest(build, "https://example.com/", only_paths=("index.html",))
    prior["pages"][0]["contract"]["h1_min"] = 3
    existing.write_text(json.dumps(prior), encoding="utf-8")
    selected = tmp_path / "next.txt"
    selected.write_text("pricing/index.html\n", encoding="utf-8")

    assert main([
        str(build), "https://example.com/", "--extend-from", str(existing),
        "--only-file", str(selected),
    ]) == 0
    expanded = json.loads(capsys.readouterr().out)
    assert expanded["pages"][0] == prior["pages"][0]
    assert expanded["pages"][1]["path"] == "pricing/index.html"


def test_curated_page_file_rejects_duplicate_across_flags(tmp_path, capsys):
    build = tmp_path / "dist"
    _html(build / "index.html")
    selected = tmp_path / "paths.txt"
    selected.write_text("index.html\n", encoding="utf-8")
    assert main([
        str(build), "https://example.com/", "--only", "index.html",
        "--only-file", str(selected),
    ]) == 2
    assert "duplicate selected path" in capsys.readouterr().err


def test_curated_page_file_rejects_oversized_list_and_invalid_utf8(tmp_path, capsys):
    build = tmp_path / "dist"
    _html(build / "index.html")
    selected = tmp_path / "paths.txt"
    selected.write_text("index.html\n" * 10_001, encoding="utf-8")
    args = [str(build), "https://example.com/", "--only-file", str(selected)]
    assert main(args) == 2
    assert "Selection file requires" in capsys.readouterr().err

    selected.write_bytes(b"\xffindex.html\n")
    assert main(args) == 2
    assert "Invalid input:" in capsys.readouterr().err


def test_extend_reviewed_manifest_preserves_old_contracts_and_adds_only_selected_pages(
    tmp_path, capsys,
):
    build = tmp_path / "dist"
    for path in ("index.html", "pricing/index.html", "legal/index.html"):
        _html(build / path)
    original = tmp_path / "reviewed.json"
    initial = generate_manifest(build, "https://example.com/", only_paths=("index.html",))
    initial["pages"][0]["contract"]["title_equals"] = "Example"
    initial["pages"][0]["contract"]["indexable"] = True
    original.write_text(json.dumps(initial), encoding="utf-8")
    before = original.read_bytes()
    destination = tmp_path / "expanded.json"

    assert main([
        str(build), "https://example.com/", "--extend-from", str(original),
        "--only", "pricing/index.html", "--expect-description",
        "--output", str(destination),
    ]) == 0
    assert capsys.readouterr().out == ""
    expanded = json.loads(destination.read_text(encoding="utf-8"))
    assert expanded["pages"][0] == initial["pages"][0]
    assert [item["path"] for item in expanded["pages"]] == [
        "index.html", "pricing/index.html",
    ]
    assert expanded["pages"][1]["contract"]["description_present"] is True
    assert original.read_bytes() == before
    assert verify_manifest(expanded, build)["coverage"]["unlisted"] == ["legal/index.html"]


@pytest.mark.parametrize("selected", [(), ("index.html",), ("missing.html",)])
def test_extend_requires_new_selected_pages_and_never_overwrites(tmp_path, capsys, selected):
    build = tmp_path / "dist"
    _html(build / "index.html")
    _html(build / "pricing/index.html")
    original = tmp_path / "reviewed.json"
    original.write_text(json.dumps(generate_manifest(
        build, "https://example.com/", only_paths=("index.html",),
    )), encoding="utf-8")
    before = original.read_bytes()
    destination = tmp_path / "expanded.json"
    args = [str(build), "https://example.com/", "--extend-from", str(original)]
    for path in selected:
        args.extend(("--only", path))
    assert main([*args, "--output", str(destination)]) == 2
    assert not destination.exists()
    assert original.read_bytes() == before
    assert "Invalid input:" in capsys.readouterr().err


def test_extend_rejects_invalid_reviewed_manifest_before_creating_output(tmp_path, capsys):
    build = tmp_path / "dist"
    _html(build / "index.html")
    _html(build / "pricing/index.html")
    original = tmp_path / "reviewed.json"
    original.write_text('{"version":1,"pages":[],"pages":[]}', encoding="utf-8")
    destination = tmp_path / "expanded.json"
    assert main([
        str(build), "https://example.com/", "--extend-from", str(original),
        "--only", "pricing/index.html", "--output", str(destination),
    ]) == 2
    assert not destination.exists()
    assert "Invalid input:" in capsys.readouterr().err


def test_extend_retains_prior_review_order_without_rechecking_into_new_contracts(tmp_path):
    build = tmp_path / "dist"
    for path in ("index.html", "pricing/index.html", "legal/index.html"):
        _html(build / path)
    original = generate_manifest(
        build, "https://example.com/", only_paths=("index.html", "pricing/index.html"),
    )
    original["pages"].reverse()
    original["pages"][0]["contract"]["h1_min"] = 2
    existing = tmp_path / "reviewed.json"
    existing.write_text(json.dumps(original), encoding="utf-8")
    new = tmp_path / "expanded.json"
    assert main([
        str(build), "https://example.com/", "--extend-from", str(existing),
        "--only", "legal/index.html", "--check-built", "--output", str(new),
    ]) == 1
    expanded = json.loads(new.read_text(encoding="utf-8"))
    assert expanded["pages"][:2] == original["pages"]
    assert expanded["pages"][2]["path"] == "legal/index.html"
    assert existing.read_text(encoding="utf-8") == json.dumps(original)


def test_extend_rejects_colliding_urls_before_output(tmp_path, capsys):
    build = tmp_path / "dist"
    _html(build / "index.html")
    _html(build / "pricing/index.html")
    existing = generate_manifest(build, "https://example.com/", only_paths=("index.html",))
    existing["pages"][0]["contract"]["url"] = "https://example.com/pricing/"
    original = tmp_path / "reviewed.json"
    original.write_text(json.dumps(existing), encoding="utf-8")
    new = tmp_path / "expanded.json"
    assert main([
        str(build), "https://example.com/", "--extend-from", str(original),
        "--only", "pricing/index.html", "--output", str(new),
    ]) == 2
    assert not new.exists()
    assert "Duplicate contract URL" in capsys.readouterr().err


@pytest.mark.parametrize("selected", [
    ("index.html", "index.html"),
    ("index.html", "missing/index.html"),
    ("../index.html",),
])
def test_selected_pages_reject_unsafe_or_duplicate_paths_without_artifacts(
    tmp_path, capsys, selected,
):
    build = tmp_path / "dist"
    _html(build / "index.html")
    output = tmp_path / "reviewed.json"
    args = [str(build), "https://example.com/", "--output", str(output)]
    for path in selected:
        args.extend(("--only", path))
    assert main(args) == 2
    assert not output.exists()
    assert "Invalid input:" in capsys.readouterr().err


def test_selected_pages_cannot_silently_ignore_unselected_exceptions(tmp_path):
    build = tmp_path / "dist"
    _html(build / "index.html")
    _html(build / "legal/index.html")
    with pytest.raises(ValueError, match="unknown expect-noindex path"):
        generate_manifest(
            build, "https://example.com/", only_paths=("index.html",),
            expect_noindex_for=("legal/index.html",),
        )


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


def test_followability_bootstrap_is_explicit_and_composes_with_indexability(tmp_path):
    _html(tmp_path / "index.html")
    basic = generate_manifest(tmp_path, "https://example.com/")
    assert "followable" not in basic["pages"][0]["contract"]

    strict = generate_manifest(
        tmp_path, "https://example.com/",
        expect_followable=True, expect_indexable=True,
    )
    assert strict["pages"][0]["contract"]["followable"] is True
    assert strict["pages"][0]["contract"]["indexable"] is True
    assert verify_manifest(strict, tmp_path)["status"] == "pass"


@pytest.mark.parametrize("placement", ("head", "body"))
@pytest.mark.parametrize("restriction", ("nofollow", "none"))
def test_followability_bootstrap_rejects_accidental_robots_nofollow(
    tmp_path, placement, restriction,
):
    page = tmp_path / "index.html"
    _html(page)
    strict = generate_manifest(tmp_path, "https://example.com/", expect_followable=True)
    meta = f'<meta name="googlebot" content="{restriction}">'
    head = meta if placement == "head" else ""
    body = meta if placement == "body" else ""
    page.write_text(f"<html><head><title>Example</title>{head}</head><body>{body}</body></html>")
    report = verify_manifest(strict, tmp_path)
    assert report["status"] == "fail"
    assert report["pages"][0]["issues"][0]["code"] == "followability_meta"
    assert verify_manifest(generate_manifest(tmp_path, "https://example.com/"), tmp_path)["status"] == "pass"


def test_followability_bootstrap_retains_indexability_independence_and_reviewed_skip(tmp_path):
    _html(tmp_path / "index.html")
    _html(tmp_path / "404.html")
    (tmp_path / "404.html").write_text(
        '<html><head><title>Not Found</title><meta name="robots" content="none"></head></html>'
    )
    (tmp_path / "index.html").write_text(
        '<html><head><title>Example</title><meta name="robots" content="noindex"></head></html>'
    )
    strict = generate_manifest(
        tmp_path, "https://example.com/", expect_followable=True,
        skip_opt_ins_for=("404.html",), expect_noindex_for=("404.html",),
    )
    contracts = {page["path"]: page["contract"] for page in strict["pages"]}
    assert "followable" not in contracts["404.html"]
    assert contracts["404.html"]["indexable"] is False
    assert contracts["index.html"]["followable"] is True
    assert verify_manifest(strict, tmp_path)["status"] == "pass"


def test_followability_cli_checks_built_html_without_network(tmp_path, capsys, monkeypatch):
    page = tmp_path / "index.html"
    _html(page)
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))
    args = [str(tmp_path), "https://example.com/", "--expect-followable", "--check-built"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["pages"][0]["contract"]["followable"] is True
    page.write_text(
        '<html><head><title>Example</title><meta name="robots" content="nofollow">'
        '</head></html>'
    )
    assert main(args) == 1
    captured = capsys.readouterr()
    assert "followability_meta" in captured.err
    assert json.loads(captured.out)["pages"][0]["contract"]["followable"] is True


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


def test_opt_in_image_alt_uses_existing_contract_and_reviewed_exceptions(tmp_path):
    build = tmp_path / "dist"
    _html(build / "index.html")
    _html(build / "404.html")
    (build / "index.html").write_text(
        '<html><head><title>Home</title></head><body><img src="hero.png"></body></html>',
        encoding="utf-8",
    )
    default = generate_manifest(build, "https://example.com/")
    assert all("img_missing_alt_max" not in p["contract"] for p in default["pages"])
    assert verify_manifest(default, build)["status"] == "pass"

    strict = generate_manifest(build, "https://example.com/", expect_image_alt=True)
    assert all(p["contract"]["img_missing_alt_max"] == 0 for p in strict["pages"])
    report = verify_manifest(strict, build)
    assert report["status"] == "fail"
    assert report["pages"][1]["issues"][0]["code"] == "img_missing_alt_max"

    # An intentionally decorative image may use an empty alt attribute.
    (build / "index.html").write_text(
        '<html><head><title>Home</title></head><body><img src="hero.png" alt=""></body></html>',
        encoding="utf-8",
    )
    assert verify_manifest(strict, build)["status"] == "pass"
    exempted = generate_manifest(
        build, "https://example.com/", expect_image_alt=True,
        skip_opt_ins_for=("404.html",),
    )
    assert "img_missing_alt_max" not in exempted["pages"][0]["contract"]
    assert exempted["pages"][1]["contract"]["img_missing_alt_max"] == 0


def test_cli_image_alt_bootstrap_check_built_is_offline_and_preserves_report(tmp_path, monkeypatch, capsys):
    build = tmp_path / "dist"
    _html(build / "index.html")
    (build / "index.html").write_text(
        '<html><head><title>Home</title></head><body><img src="bad.png"></body></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))
    first = tmp_path / "first.json"
    args = [str(build), "https://example.com/", "--expect-image-alt", "--check-built"]
    assert main(args + ["--output", str(first)]) == 1
    assert json.loads(first.read_text())["pages"][0]["contract"]["img_missing_alt_max"] == 0
    assert "img_missing_alt_max" in capsys.readouterr().err

    (build / "index.html").write_text(
        '<html><head><title>Home</title></head><body><img src="ok.png" alt="Graphic"></body></html>',
        encoding="utf-8",
    )
    assert main(args + ["--output", str(tmp_path / "second.json")]) == 0
    assert "PASS" in capsys.readouterr().err
    assert main(args + ["--output", str(first)]) == 2
    assert "Invalid input:" in capsys.readouterr().err


def test_bootstrap_language_attribute_presence_for_multilingual_builds(tmp_path):
    build = tmp_path / "dist"
    _html(build / "en/index.html")
    _html(build / "sl/index.html")
    (build / "en/index.html").write_text(
        '<html lang="en-GB"><head><title>English</title></head></html>', encoding="utf-8",
    )
    (build / "sl/index.html").write_text(
        '<html lang="sl"><head><title>Slovensko</title></head></html>', encoding="utf-8",
    )
    default = generate_manifest(build, "https://example.com/")
    assert all("html_lang_present" not in p["contract"] for p in default["pages"])
    strict = generate_manifest(build, "https://example.com/", expect_lang_attribute=True)
    assert all(p["contract"]["html_lang_present"] is True for p in strict["pages"])
    assert verify_manifest(strict, build, require_full_coverage=True)["status"] == "pass"

    (build / "sl/index.html").write_text(
        '<html lang=""><head><title>Slovensko</title></head></html>', encoding="utf-8",
    )
    report = verify_manifest(strict, build)
    assert report["status"] == "fail"
    assert report["pages"][1]["issues"][0]["code"] == "html_lang_presence"

    excepted = generate_manifest(
        build, "https://example.com/", expect_lang_attribute=True,
        skip_opt_ins_for=("sl/index.html",),
    )
    assert excepted["pages"][0]["contract"]["html_lang_present"] is True
    assert "html_lang_present" not in excepted["pages"][1]["contract"]
    assert verify_manifest(excepted, build)["status"] == "pass"


def test_bootstrap_language_attribute_cli_check_built_is_offline(tmp_path, monkeypatch, capsys):
    build = tmp_path / "dist"
    _html(build / "index.html")
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))
    args = [str(build), "https://example.com/", "--expect-lang-attribute", "--check-built"]
    manifest_path = tmp_path / "manifest.json"
    assert main(args + ["--output", str(manifest_path)]) == 1
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["pages"][0]["contract"][
        "html_lang_present"
    ] is True
    assert "html_lang_presence" in capsys.readouterr().err
    (build / "index.html").write_text(
        '<html lang="en"><head><title>Example</title></head></html>', encoding="utf-8",
    )
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["pages"][0]["contract"][
        "html_lang_present"
    ] is True
    assert main(args + ["--output", str(manifest_path)]) == 2
    assert "Invalid input:" in capsys.readouterr().err


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


def test_reviewed_language_map_bootstraps_exact_multilingual_contracts(tmp_path):
    build = tmp_path / "dist"
    _html(build / "en" / "index.html")
    _html(build / "sl" / "index.html")
    _html(build / "404.html")
    for name, lang in (("en", "en-GB"), ("sl", "sl")):
        (build / name / "index.html").write_text(
            f'<html lang="{lang}"><head><title>{name}</title></head></html>',
            encoding="utf-8",
        )
    manifest = generate_manifest(
        build, "https://example.com/", language_overrides={
            "en/index.html": "en-GB", "sl/index.html": "sl",
        }, skip_opt_ins_for=("404.html",),
    )
    assert [p["contract"].get("html_lang") for p in manifest["pages"]] == [
        None, "en-GB", "sl",
    ]
    assert verify_manifest(manifest, build, require_full_coverage=True)["status"] == "pass"
    (build / "sl" / "index.html").write_text(
        '<html lang="en"><head><title>sl</title></head></html>', encoding="utf-8",
    )
    report = verify_manifest(manifest, build, require_full_coverage=True)
    assert report["status"] == "fail"
    assert report["pages"][-1]["issues"][0]["code"] == "html_lang"


@pytest.mark.parametrize("language_map", [
    {"en/index.html": "en"},  # Missing reviewed page requires an explicit skip.
    {"en/index.html": "en", "sl/index.html": "sl", "other.html": "fr"},
    {"en/index.html": "en", "sl/index.html": "en GB"},
    {"en/index.html": "en", "sl/index.html": ""},
    {"en/index.html": "en", "sl/index.html": 42},
    {"en/index.html": "en", "sl/index.html": "en--GB"},
    {"../en/index.html": "en", "sl/index.html": "sl"},
])
def test_reviewed_language_map_rejects_unsafe_or_incomplete_policy(tmp_path, language_map):
    _html(tmp_path / "en" / "index.html")
    _html(tmp_path / "sl" / "index.html")
    with pytest.raises(ValueError, match="language|lang|unknown|Unsafe"):
        generate_manifest(
            tmp_path, "https://example.com/", language_overrides=language_map,
        )


def test_cli_reviewed_language_map_is_offline_and_fail_closed(tmp_path, monkeypatch, capsys):
    build = tmp_path / "dist"
    _html(build / "en" / "index.html")
    _html(build / "sl" / "index.html")
    for language in ("en", "sl"):
        (build / language / "index.html").write_text(
            f'<html lang="{language}"><head><title>Example</title></head></html>',
            encoding="utf-8",
        )
    language_file = tmp_path / "languages.json"
    destination = tmp_path / "manifest.json"
    language_file.write_text(
        '{"version":1,"languages":{"en/index.html":"en","sl/index.html":"sl"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))
    args = [str(build), "https://example.com/", "--lang-map", str(language_file),
            "--check-built", "--output", str(destination)]
    assert main(args) == 0
    assert "PASS 2/2" in capsys.readouterr().err
    manifest = json.loads(destination.read_text(encoding="utf-8"))
    assert [page["contract"]["html_lang"] for page in manifest["pages"]] == ["en", "sl"]
    destination.unlink()
    for bad in (
        '{"version":1,"languages":{"en/index.html":"en","en/index.html":"fr"}}',
        '{"version":true,"languages":{"en/index.html":"en","sl/index.html":"sl"}}',
        '{"version":1,"languages":{"en/index.html":"en"}}',
        '{"version":1,"languages":{"en/index.html":"en","sl/index.html":"en GB"}}',
        '{"version":1,"languages":{}}',
        '{',
    ):
        language_file.write_text(bad, encoding="utf-8")
        assert main(args) == 2
        assert not destination.exists()
        assert "Invalid input:" in capsys.readouterr().err


def test_reviewed_hreflang_clusters_generate_exact_reciprocal_urls(tmp_path):
    build = tmp_path / "dist"
    _html(build / "en" / "index.html")
    _html(build / "sl" / "index.html")
    _html(build / "about.html")
    expected = {
        "en": "https://example.com/docs/en/",
        "sl": "https://example.com/docs/slovenian/",
        "x-default": "https://example.com/docs/en/",
    }
    for page in ("en/index.html", "sl/index.html"):
        links = "".join(
            f'<link rel="alternate" hreflang="{lang}" href="{url}">'
            for lang, url in expected.items()
        )
        (build / page).write_text(
            f"<html><head><title>Example</title>{links}</head></html>",
            encoding="utf-8",
        )
    manifest = generate_manifest(
        build, "https://example.com/docs/",
        route_overrides={"sl/index.html": "slovenian/"},
        hreflang_clusters=[{
            "en": "en/index.html",
            "sl": "sl/index.html",
            "x-default": "en/index.html",
        }],
    )
    assert [page["contract"].get("hreflang") for page in manifest["pages"]] == [
        None, expected, expected,
    ]
    report = verify_manifest(
        manifest, build, require_full_coverage=True,
        check_hreflang_reciprocity=True, check_hreflang_clusters=True,
    )
    assert report["status"] == "pass"
    (build / "sl/index.html").write_text(
        '<html><head><title>Example</title></head></html>', encoding="utf-8",
    )
    report = verify_manifest(manifest, build, require_full_coverage=True)
    assert report["status"] == "fail"


@pytest.mark.parametrize("clusters", [
    [{"en": "en/index.html"}],
    [{"en": "en/index.html", "sl": "missing/index.html"}],
    [{"en": "../en/index.html", "sl": "sl/index.html"}],
    [{"en": "en/index.html", "EN": "sl/index.html"}],
    [{"en": "en/index.html", "sl": "en/index.html"}],
    [{"en": "en/index.html", "sl": "sl/index.html"}, {
        "fr": "en/index.html", "de": "sl/index.html",
    }],
    [{"en": "en/index.html", "sl": 3}],
    [{"en": "en/index.html", "en GB": "sl/index.html"}],
])
def test_reviewed_hreflang_clusters_reject_incomplete_or_ambiguous_inputs(tmp_path, clusters):
    _html(tmp_path / "en/index.html")
    _html(tmp_path / "sl/index.html")
    with pytest.raises(ValueError):
        generate_manifest(
            tmp_path, "https://example.com/", hreflang_clusters=clusters,
        )


def test_reviewed_hreflang_clusters_reject_opt_in_skip_overlap(tmp_path):
    _html(tmp_path / "en/index.html")
    _html(tmp_path / "sl/index.html")
    with pytest.raises(ValueError, match="skipp|skip"):
        generate_manifest(
            tmp_path, "https://example.com/",
            skip_opt_ins_for=("sl/index.html",),
            hreflang_clusters=[{"en": "en/index.html", "sl": "sl/index.html"}],
        )


def test_cli_reviewed_hreflang_map_is_offline_and_fail_closed(tmp_path, capsys, monkeypatch):
    build = tmp_path / "dist"
    _html(build / "en/index.html")
    _html(build / "sl/index.html")
    for language in ("en", "sl"):
        links = "".join(
            f'<link rel="alternate" hreflang="{lang}" '
            f'href="https://example.com/{lang}/">'
            for lang in ("en", "sl")
        )
        (build / language / "index.html").write_text(
            f"<html><head><title>Example</title>{links}</head></html>",
            encoding="utf-8",
        )
    mapping = tmp_path / "hreflang.json"
    output = tmp_path / "manifest.json"
    args = [str(build), "https://example.com/", "--hreflang-map", str(mapping),
            "--check-built", "--output", str(output)]
    mapping.write_text(
        '{"version":1,"clusters":[{"en":"en/index.html","sl":"sl/index.html"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("network used"))
    assert main(args) == 0
    assert "PASS 2/2" in capsys.readouterr().err
    expected = {"en": "https://example.com/en/", "sl": "https://example.com/sl/"}
    assert all(
        page["contract"]["hreflang"] == expected
        for page in json.loads(output.read_text(encoding="utf-8"))["pages"]
    )
    output.unlink()
    for bad in (
        '{"version":1,"clusters":[{"en":"en/index.html","en":"sl/index.html"}]}',
        '{"version":true,"clusters":[{"en":"en/index.html","sl":"sl/index.html"}]}',
        '{"version":1,"clusters":[{"en":"en/index.html","sl":"absent.html"}]}',
        '{"version":1,"clusters":[]}',
        '{',
    ):
        mapping.write_text(bad, encoding="utf-8")
        assert main(args) == 2
        assert not output.exists()
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
