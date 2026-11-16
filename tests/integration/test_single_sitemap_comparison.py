"""Opt-in regression comparison of ordinary one-file sitemap URL sets."""

import gzip
import json
import os
import socket
from html import escape

import pytest

from searcharis.sitemap_compare import main

_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
_HOME = "https://example.test/"
_ABOUT = "https://example.test/about"
_DRAFT = "https://example.test/draft"


def _manifest(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "pages": [
            {"path": "index.html", "contract": {"url": _HOME, "indexable": True}},
            {"path": "about.html", "contract": {"url": _ABOUT, "indexable": True}},
            {"path": "draft.html", "contract": {"url": _DRAFT, "indexable": False}},
        ],
    }), encoding="utf-8")
    return manifest


def _urlset(tmp_path, label, urls, *, gz=False):
    directory = tmp_path / label
    directory.mkdir()
    file = directory / ("sitemap.xml.gz" if gz else "sitemap.xml")
    xml = (
        f'<urlset xmlns="{_NS}">'
        + "".join(f"<url><loc>{escape(url)}</loc></url>" for url in urls)
        + "</urlset>"
    ).encode("utf-8")
    file.write_bytes(gzip.compress(xml, mtime=0) if gz else xml)
    return file


def _run(tmp_path, capsys, baseline, candidate, *options):
    result = main([
        str(_manifest(tmp_path)), str(baseline), str(candidate),
        "--single-sitemap", "--json", *options,
    ])
    output = capsys.readouterr()
    assert output.err == ""
    return result, json.loads(output.out)


@pytest.mark.parametrize("gz", [False, True])
def test_single_urlsets_allow_inherited_debt_without_network(tmp_path, capsys, monkeypatch, gz):
    monkeypatch.setattr(
        socket, "socket",
        lambda *args, **kwargs: pytest.fail("Offline comparator must never fetch a URL"),
    )
    baseline = _urlset(tmp_path, "base", [_HOME], gz=gz)
    candidate = _urlset(tmp_path, "candidate", [_HOME], gz=gz)
    code, report = _run(tmp_path, capsys, baseline, candidate)
    assert code == 0
    assert report["status"] == "pass"
    assert report["inherited"]["missing_required"] == [_ABOUT]
    assert report["new"]["missing_required"] == []


def test_new_missing_required_page_blocks(tmp_path, capsys):
    baseline = _urlset(tmp_path, "base", [_HOME, _ABOUT])
    candidate = _urlset(tmp_path, "candidate", [_HOME])
    code, report = _run(tmp_path, capsys, baseline, candidate)
    assert code == 1
    assert report["new"]["missing_required"] == [_ABOUT]


def test_new_noindex_page_blocks_and_unreviewed_is_opt_in(tmp_path, capsys):
    baseline = _urlset(tmp_path, "base", [_HOME])
    candidate = _urlset(tmp_path, "candidate", [_HOME, _DRAFT, "https://example.test/new"])
    code, report = _run(tmp_path, capsys, baseline, candidate)
    assert code == 1
    assert report["new"]["present_excluded"] == [_DRAFT]
    assert "unreviewed_urls" not in report["new"]


def test_optional_unreviewed_gate_preserves_url_aliases(tmp_path, capsys):
    baseline = _urlset(tmp_path, "base", [_HOME, "https://EXAMPLE.test/%7E"])
    candidate = _urlset(tmp_path, "candidate", [_HOME, "https://example.test/~"])
    code, report = _run(
        tmp_path, capsys, baseline, candidate, "--reject-unreviewed-urls",
    )
    assert code == 0
    assert report["new"]["unreviewed_urls"] == []


def test_empty_candidate_is_a_new_missing_page_not_an_input_error(tmp_path, capsys):
    baseline = _urlset(tmp_path, "base", [_HOME, _ABOUT])
    candidate = _urlset(tmp_path, "candidate", [])
    code, report = _run(tmp_path, capsys, baseline, candidate)
    assert code == 1
    assert report["new"]["missing_required"] == [_HOME, _ABOUT]


def test_different_public_sites_fail_closed(tmp_path, capsys):
    baseline = _urlset(tmp_path, "base", [_HOME])
    candidate = _urlset(tmp_path, "candidate", ["https://different.test/"])
    code, report = _run(tmp_path, capsys, baseline, candidate)
    assert code == 2
    assert "same site" in report["error"]


def test_empty_baseline_cannot_make_unrelated_candidate_look_safe(tmp_path, capsys):
    baseline = _urlset(tmp_path, "base", [])
    candidate = _urlset(tmp_path, "candidate", ["https://different.test/"])
    code, report = _run(tmp_path, capsys, baseline, candidate)
    assert code == 2
    assert "nonempty site identity" in report["error"]


def test_malformed_single_gzip_fails_as_input_error(tmp_path, capsys):
    baseline = _urlset(tmp_path, "base", [_HOME], gz=True)
    candidate = _urlset(tmp_path, "candidate", [_HOME], gz=True)
    candidate.write_bytes(b"not gzip data")
    code, report = _run(tmp_path, capsys, baseline, candidate)
    assert code == 2
    assert "gzip" in report["error"]


def test_unreviewed_file_extension_is_not_accepted(tmp_path, capsys):
    baseline = _urlset(tmp_path, "base", [_HOME])
    candidate = _urlset(tmp_path, "candidate", [_HOME])
    other = candidate.with_suffix(".txt")
    candidate.rename(other)
    code, report = _run(tmp_path, capsys, baseline, other)
    assert code == 2
    assert ".xml" in report["error"]


def test_hardlinked_snapshot_fails_closed(tmp_path, capsys):
    baseline = _urlset(tmp_path, "base", [_HOME])
    candidate = _urlset(tmp_path, "candidate", [_HOME])
    candidate.unlink()
    try:
        os.link(baseline, candidate)
    except OSError as error:
        pytest.skip(f"Hardlinks unavailable: {error}")
    code, report = _run(tmp_path, capsys, baseline, candidate)
    assert code == 2
    assert "hard-linked" in report["error"]


def test_symlinked_snapshot_fails_closed(tmp_path, capsys):
    baseline = _urlset(tmp_path, "base", [_HOME])
    candidate = _urlset(tmp_path, "candidate", [_HOME])
    candidate.unlink()
    try:
        candidate.symlink_to(baseline)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"Symlinks unavailable: {error}")
    code, report = _run(tmp_path, capsys, baseline, candidate)
    assert code == 2
    assert "regular local file" in report["error"]


def test_json_artifact_remains_outside_both_build_roots(tmp_path, capsys):
    baseline = _urlset(tmp_path, "base", [_HOME])
    candidate = _urlset(tmp_path, "candidate", [_HOME])
    forbidden = candidate.parent / "comparison.json"
    assert main([
        str(_manifest(tmp_path)), str(baseline), str(candidate),
        "--single-sitemap", "--json-file", str(forbidden),
    ]) == 2
    assert not forbidden.exists()
    assert capsys.readouterr().out == ""


def test_json_artifact_exports_regression_in_safe_sibling(tmp_path, capsys):
    baseline = _urlset(tmp_path, "base", [_HOME, _ABOUT])
    candidate = _urlset(tmp_path, "candidate", [_HOME])
    artifact = tmp_path / "results.json"
    assert main([
        str(_manifest(tmp_path)), str(baseline), str(candidate),
        "--single-sitemap", "--json-file", str(artifact),
    ]) == 1
    assert capsys.readouterr().out == ""
    assert json.loads(artifact.read_text(encoding="utf-8"))["new"]["missing_required"] == [
        _ABOUT,
    ]


def test_plain_urlset_requires_explicit_opt_in(tmp_path, capsys):
    baseline = _urlset(tmp_path, "base", [_HOME])
    candidate = _urlset(tmp_path, "candidate", [_HOME])
    assert main([str(_manifest(tmp_path)), str(baseline), str(candidate), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"
