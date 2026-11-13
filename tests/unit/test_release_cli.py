"""An opt-in offline gate for reviewed built HTML and local sitemap intent."""

import json
import socket
import subprocess
import sys

import pytest

from searcharis.release_cli import main


def _fixture(tmp_path):
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text(
        '<html><head><title>Home</title>'
        '<link rel="canonical" href="https://example.test/">'
        '</head><body><h1>Home</h1></body></html>',
        encoding="utf-8",
    )
    (build / "draft.html").write_text(
        '<html><head><title>Draft</title>'
        '<link rel="canonical" href="https://example.test/">'
        '<meta name="robots" content="noindex">'
        '</head><body></body></html>',
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": 1, "pages": [
        {"path": "index.html", "contract": {
            "url": "https://example.test/", "title_present": True,
            "canonical": "https://example.test/", "indexable": True,
        }},
        {"path": "draft.html", "contract": {
            "url": "https://example.test/draft", "title_present": True,
            "indexable": False,
        }},
    ]}), encoding="utf-8")
    sitemap = tmp_path / "sitemap.xml"
    _sitemap(sitemap, ["https://example.test/"])
    return manifest, build, sitemap


def _sitemap(path, urls):
    payload = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    payload += "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    path.write_text(payload + "</urlset>", encoding="utf-8")


def _call(capsys, manifest, build, sitemaps, *options):
    code = main([str(manifest), str(build), *(str(p) for p in sitemaps),
                 "--json", *options])
    output = capsys.readouterr()
    return code, json.loads(output.out), output.err


def test_both_checks_pass_and_report_both_results(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    code, report, err = _call(capsys, manifest, build, [sitemap],
                              "--require-full-coverage")
    assert code == 0 and err == ""
    assert report["status"] == "pass"
    assert report["site"]["summary"]["checked"] == 2
    assert report["sitemap"]["summary"]["required"] == 1
    assert report["sitemap"]["summary"]["excluded"] == 1


def test_html_regression_blocks_when_sitemap_passes(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    (build / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
    code, report, _ = _call(capsys, manifest, build, [sitemap])
    assert code == 1
    assert report["status"] == "fail"
    assert report["site"]["status"] == "fail"
    assert report["sitemap"]["status"] == "pass"


def test_sitemap_regression_blocks_when_built_html_passes(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    _sitemap(sitemap, ["https://example.test/draft"])
    code, report, _ = _call(capsys, manifest, build, [sitemap])
    assert code == 1
    assert report["site"]["status"] == "pass"
    assert report["sitemap"]["status"] == "fail"
    assert report["sitemap"]["missing_required"] == ["https://example.test/"]
    assert report["sitemap"]["present_excluded"] == ["https://example.test/draft"]


def test_both_regressions_stay_visible(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    (build / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
    _sitemap(sitemap, ["https://example.test/draft"])
    code, report, _ = _call(capsys, manifest, build, [sitemap])
    assert code == 1
    assert report["site"]["status"] == "fail"
    assert report["sitemap"]["status"] == "fail"


@pytest.mark.parametrize("invalid", ["missing", "malformed", "unasserted"])
def test_invalid_inputs_fail_closed_not_masked_by_valid_other_check(
    tmp_path, capsys, invalid,
):
    manifest, build, sitemap = _fixture(tmp_path)
    if invalid == "missing":
        sitemap = tmp_path / "absent.xml"
    elif invalid == "malformed":
        sitemap.write_text("<xml/>", encoding="utf-8")
    else:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for page in data["pages"]:
            del page["contract"]["indexable"]
        manifest.write_text(json.dumps(data), encoding="utf-8")
    code, report, err = _call(capsys, manifest, build, [sitemap])
    assert code == 2 and err == ""
    assert report["status"] == "error"
    assert isinstance(report["error"], str) and report["error"]


def test_combined_gate_supports_explicit_split_local_sitemaps(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    other = tmp_path / "second.xml"
    _sitemap(sitemap, [])
    _sitemap(other, ["https://example.test/"])
    code, report, _ = _call(capsys, manifest, build, [sitemap, other])
    assert code == 0
    assert report["sitemap"]["summary"]["sitemap_urls"] == 1


def test_coverage_option_applies_to_site_without_skipping_sitemap(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    (build / "unlisted.html").write_text("<html/>", encoding="utf-8")
    code, report, _ = _call(capsys, manifest, build, [sitemap],
                            "--require-full-coverage")
    assert code == 1
    assert report["site"]["coverage"]["unlisted"] == ["unlisted.html"]
    assert report["sitemap"]["status"] == "pass"


def test_cross_page_checks_use_the_existing_site_verifier(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    code, report, _ = _call(
        capsys, manifest, build, [sitemap], "--check-canonical-consistency",
        "--check-hreflang-reciprocity", "--check-hreflang-clusters",
    )
    assert code == 0
    assert report["site"]["canonical_consistency"]["internal_targets_checked"] >= 1
    assert report["site"]["hreflang_reciprocity"]["checked_pairs"] == 0
    assert report["site"]["hreflang_clusters"]["checked_pairs"] == 0


def test_duplicate_alias_across_sitemap_shards_is_input_error(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    other = tmp_path / "second.xml"
    _sitemap(other, ["https://EXAMPLE.test:443/"])
    code, report, _ = _call(capsys, manifest, build, [sitemap, other])
    assert code == 2
    assert report["status"] == "error"
    assert "Duplicate sitemap" in report["error"]


def test_human_summary_and_no_outbound_urls(tmp_path, capsys, monkeypatch):
    manifest, build, sitemap = _fixture(tmp_path)

    def refuse_connect(*_args, **_kwargs):
        raise AssertionError("Offline CLI must never connect to the network")

    monkeypatch.setattr(socket.socket, "connect", refuse_connect)
    code = main([str(manifest), str(build), str(sitemap)])
    result = capsys.readouterr()
    assert code == 0 and result.err == ""
    assert "PASS" in result.out
    assert "HTML" in result.out and "sitemap" in result.out


def test_python_module_entrypoint_is_ci_consumable(tmp_path):
    manifest, build, sitemap = _fixture(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "searcharis.release_cli", str(manifest),
         str(build), str(sitemap), "--json"],
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert json.loads(result.stdout)["status"] == "pass"
