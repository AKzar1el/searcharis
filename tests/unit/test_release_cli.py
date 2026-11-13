"""An opt-in offline gate for reviewed built HTML and local sitemap intent."""

import gzip
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


def test_combined_gate_accepts_local_gzip_sitemap(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    compressed = tmp_path / "sitemap.xml.gz"
    compressed.write_bytes(gzip.compress(sitemap.read_bytes(), mtime=0))
    code, report, err = _call(capsys, manifest, build, [compressed],
                              "--require-full-coverage")
    assert code == 0 and err == ""
    assert report["site"]["status"] == "pass"
    assert report["sitemap"]["status"] == "pass"


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


@pytest.mark.parametrize("regression", [False, True])
def test_json_file_writes_complete_utf8_report_without_stdout(
    tmp_path, capsys, regression,
):
    manifest, build, sitemap = _fixture(tmp_path)
    if regression:
        _sitemap(sitemap, ["https://example.test/draft"])
    expected_code, expected_report, _ = _call(capsys, manifest, build, [sitemap])
    target = tmp_path / "release-result.json"

    code = main([str(manifest), str(build), str(sitemap), "--json-file", str(target)])
    output = capsys.readouterr()

    assert code == expected_code
    assert output.out == output.err == ""
    raw = target.read_bytes()
    assert raw.startswith(b"{\n") and b"\x00" not in raw
    assert json.loads(raw.decode("utf-8")) == expected_report


@pytest.mark.parametrize("target_name", ["existing.json", "dist/report.json"])
def test_json_file_never_overwrites_or_writes_into_build(
    tmp_path, capsys, target_name,
):
    manifest, build, sitemap = _fixture(tmp_path)
    target = tmp_path / target_name
    if target_name == "existing.json":
        target.write_bytes(b"prior evidence")
    code = main([str(manifest), str(build), str(sitemap), "--json-file", str(target)])
    output = capsys.readouterr()
    assert code == 2 and output.out == ""
    assert "Invalid input" in output.err
    if target_name == "existing.json":
        assert target.read_bytes() == b"prior evidence"
    else:
        assert not target.exists()


def test_json_file_input_failure_is_not_a_false_pass(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    target = tmp_path / "report.json"
    code = main([
        str(manifest), str(build), str(tmp_path / "missing.xml"),
        "--json-file", str(target),
    ])
    output = capsys.readouterr()
    assert code == 2 and output.out == ""
    assert "Invalid input" in output.err
    assert not target.exists()


def test_json_file_and_stdout_json_are_exclusive(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    with pytest.raises(SystemExit) as error:
        main([str(manifest), str(build), str(sitemap), "--json", "--json-file",
              str(tmp_path / "report.json")])
    assert error.value.code == 2
    assert "not allowed" in capsys.readouterr().err


def test_python_module_can_create_release_json_artifact(tmp_path):
    manifest, build, sitemap = _fixture(tmp_path)
    target = tmp_path / "ci-release.json"
    result = subprocess.run(
        [sys.executable, "-m", "searcharis.release_cli", str(manifest),
         str(build), str(sitemap), "--json-file", str(target)],
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "pass"
