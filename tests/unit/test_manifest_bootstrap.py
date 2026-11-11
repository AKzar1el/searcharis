"""Bootstrap a useful offline SEO manifest from local build artifacts only."""

import json
import socket

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


def test_starter_title_policy_fails_when_built_title_is_missing(tmp_path):
    _html(tmp_path / "index.html")
    (tmp_path / "index.html").write_text("<html><head></head><body></body></html>")
    manifest = generate_manifest(tmp_path, "https://example.com/")
    report = verify_manifest(manifest, tmp_path, require_full_coverage=True)
    assert report["status"] == "fail"
    assert report["pages"][0]["issues"][0]["code"] == "title_presence"


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
])
def test_invalid_or_ambiguous_base_urls_fail_closed(tmp_path, base):
    _html(tmp_path / "index.html")
    with pytest.raises(ValueError, match="base URL"):
        generate_manifest(tmp_path, base)


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
