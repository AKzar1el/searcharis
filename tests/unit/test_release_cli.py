"""An opt-in offline gate for reviewed built HTML and local sitemap intent."""

import gzip
import json
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from searcharis.release_cli import main
from searcharis.site_junit import build_release_junit


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


def test_release_gate_strict_indexability_coverage_accepts_reviewed_policy(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    code, report, err = _call(
        capsys, manifest, build, [sitemap], "--require-indexability-coverage",
    )
    assert code == 0 and err == ""
    assert report["sitemap"]["summary"]["unasserted_pages"] == 0


def test_release_gate_strict_indexability_coverage_fails_unasserted_policy(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    del data["pages"][1]["contract"]["indexable"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert _call(capsys, manifest, build, [sitemap])[0] == 0
    code, report, err = _call(
        capsys, manifest, build, [sitemap], "--require-indexability-coverage",
    )
    assert code == 2 and err == ""
    assert report["status"] == "error"
    assert "explicit indexability" in report["error"]


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


def test_release_gate_optional_noncanonical_alias_guard(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    (build / "alias.html").write_text(
        '<html><head><title>Alias</title>'
        '<link rel="canonical" href="https://example.test/">'
        '</head><body><h1>Alias</h1></body></html>',
        encoding="utf-8",
    )
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["pages"].append({"path": "alias.html", "contract": {
        "url": "https://example.test/alias", "title_present": True,
        "canonical": "https://example.test/", "indexable": True,
    }})
    manifest.write_text(json.dumps(data), encoding="utf-8")
    _sitemap(sitemap, ["https://example.test/", "https://example.test/alias"])
    assert _call(capsys, manifest, build, [sitemap])[0] == 0
    code, report, err = _call(
        capsys, manifest, build, [sitemap], "--reject-noncanonical-urls",
    )
    assert code == 1 and err == ""
    assert report["site"]["status"] == "pass"
    assert report["sitemap"]["present_noncanonical"] == ["https://example.test/alias"]


def test_release_gate_opt_in_rejects_unreviewed_sitemap_url(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    _sitemap(sitemap, ["https://example.test/", "https://example.test/stale"])
    assert _call(capsys, manifest, build, [sitemap])[0] == 0
    code, report, err = _call(
        capsys, manifest, build, [sitemap], "--reject-unreviewed-urls",
    )
    assert code == 1 and err == ""
    assert report["site"]["status"] == "pass"
    assert report["sitemap"]["unreviewed_urls"] == ["https://example.test/stale"]


def test_release_junit_includes_unreviewed_sitemap_url_failure(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    _sitemap(sitemap, ["https://example.test/", "https://example.test/stale"])
    code = main([
        str(manifest), str(build), str(sitemap), "--reject-unreviewed-urls", "--junit",
    ])
    output = capsys.readouterr()
    assert code == 1 and output.err == ""
    xml = ET.fromstring(output.out)
    failures = xml.findall(".//failure")
    assert len(failures) == 1
    assert "unreviewed_urls" in xml.find("testcase").attrib["name"]


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


def test_release_gate_rejects_mixed_scheme_within_one_sitemap(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    _sitemap(sitemap, ["https://example.test/", "http://example.test/other"])
    code, report, err = _call(capsys, manifest, build, [sitemap])
    assert code == 2 and err == ""
    assert report["status"] == "error"
    assert "same scheme" in report["error"]


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


def test_release_junit_stdout_pass_is_parseable(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    code = main([str(manifest), str(build), str(sitemap), "--junit"])
    result = capsys.readouterr()
    suite = ET.fromstring(result.out)
    assert code == 0 and result.err == ""
    assert suite.attrib["tests"] == "1"
    assert suite.attrib["failures"] == "0"
    assert suite.find("testcase/failure") is None


def test_release_junit_reports_html_and_sitemap_failures_separately(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    (build / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
    _sitemap(sitemap, ["https://example.test/draft"])
    code = main([str(manifest), str(build), str(sitemap), "--junit"])
    result = capsys.readouterr()
    suite = ET.fromstring(result.out)
    failures = suite.findall("testcase/failure")
    names = [case.attrib["name"] for case in suite.findall("testcase")]
    assert code == 1 and result.err == ""
    assert int(suite.attrib["failures"]) == len(failures) == len(names)
    assert len(failures) >= 4
    assert any("html" in name for name in names)
    assert any("sitemap" in name for name in names)
    assert any("missing_required" in name for name in names)
    assert any("present_excluded" in name for name in names)


@pytest.mark.parametrize("failing", [False, True])
def test_release_junit_file_is_parseable_utf8_and_preserves_exit_code(tmp_path, capsys, failing):
    manifest, build, sitemap = _fixture(tmp_path)
    if failing:
        _sitemap(sitemap, ["https://example.test/draft"])
    target = tmp_path / "release-junit.xml"
    code = main([str(manifest), str(build), str(sitemap), "--junit-file", str(target)])
    result = capsys.readouterr()
    raw = target.read_bytes()
    suite = ET.fromstring(raw)
    assert code == (1 if failing else 0)
    assert result.out == result.err == ""
    assert raw.startswith(b"<?xml") and b"\x00" not in raw
    assert int(suite.attrib["failures"]) > 0 if failing else suite.attrib["failures"] == "0"


@pytest.mark.parametrize("destination", ["existing.xml", "dist/report.xml"])
def test_release_junit_file_refuses_existing_or_build_path(tmp_path, capsys, destination):
    manifest, build, sitemap = _fixture(tmp_path)
    target = tmp_path / destination
    if destination == "existing.xml":
        target.write_bytes(b"protected prior result")
    code = main([str(manifest), str(build), str(sitemap), "--junit-file", str(target)])
    result = capsys.readouterr()
    assert code == 2 and result.out == ""
    assert "Invalid input" in result.err
    if destination == "existing.xml":
        assert target.read_bytes() == b"protected prior result"
    else:
        assert not target.exists()


def test_release_junit_input_error_does_not_emit_false_report(tmp_path, capsys):
    manifest, build, _ = _fixture(tmp_path)
    target = tmp_path / "release-junit.xml"
    code = main([str(manifest), str(build), str(tmp_path / "missing.xml"),
                 "--junit-file", str(target)])
    result = capsys.readouterr()
    assert code == 2 and result.out == ""
    assert "Invalid input" in result.err
    assert not target.exists()


def test_release_junit_modes_are_mutually_exclusive(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    for incompatible in ("--json", "--junit"):
        with pytest.raises(SystemExit) as error:
            main([str(manifest), str(build), str(sitemap), incompatible,
                  "--junit-file", str(tmp_path / "report.xml")])
        assert error.value.code == 2
        assert "not allowed" in capsys.readouterr().err


@pytest.mark.parametrize("failing", [False, True])
def test_release_writes_json_and_junit_in_one_offline_run(tmp_path, capsys, failing,
                                                       monkeypatch):
    manifest, build, sitemap = _fixture(tmp_path)
    if failing:
        _sitemap(sitemap, ["https://example.test/draft"])
    json_path = tmp_path / "result.json"
    junit_path = tmp_path / "result.xml"

    def deny(*_args, **_kwargs):
        raise AssertionError("Release artifact generation must not fetch URLs")

    monkeypatch.setattr(socket.socket, "connect", deny)
    code = main([str(manifest), str(build), str(sitemap),
                 "--json-file", str(json_path), "--junit-file", str(junit_path)])
    output = capsys.readouterr()
    report = json.loads(json_path.read_text(encoding="utf-8"))
    suite = ET.fromstring(junit_path.read_bytes())
    assert code == (1 if failing else 0)
    assert output.out == output.err == ""
    assert report["status"] == ("fail" if failing else "pass")
    assert int(suite.attrib["failures"]) > 0 if failing else suite.attrib["failures"] == "0"


@pytest.mark.parametrize("blocked", ["existing_json", "existing_xml", "build_json",
                                         "build_xml", "same_path"])
def test_dual_release_outputs_preflight_both_paths_before_any_write(
    tmp_path, capsys, blocked,
):
    manifest, build, sitemap = _fixture(tmp_path)
    json_path = tmp_path / "result.json"
    junit_path = tmp_path / "result.xml"
    if blocked == "existing_json":
        json_path.write_bytes(b"prior JSON")
    elif blocked == "existing_xml":
        junit_path.write_bytes(b"prior JUnit")
    elif blocked == "build_json":
        json_path = build / "report.json"
    elif blocked == "build_xml":
        junit_path = build / "report.xml"
    else:
        junit_path = json_path
    before_json = json_path.read_bytes() if json_path.exists() else None
    before_junit = junit_path.read_bytes() if junit_path.exists() else None
    code = main([str(manifest), str(build), str(sitemap),
                 "--json-file", str(json_path), "--junit-file", str(junit_path)])
    output = capsys.readouterr()
    assert code == 2 and output.out == "" and "Invalid input" in output.err
    assert (json_path.read_bytes() if json_path.exists() else None) == before_json
    assert (junit_path.read_bytes() if junit_path.exists() else None) == before_junit


def test_dual_release_input_error_creates_neither_output(tmp_path, capsys):
    manifest, build, _ = _fixture(tmp_path)
    json_path = tmp_path / "result.json"
    junit_path = tmp_path / "result.xml"
    code = main([str(manifest), str(build), str(tmp_path / "missing.xml"),
                 "--json-file", str(json_path), "--junit-file", str(junit_path)])
    assert code == 2 and "Invalid input" in capsys.readouterr().err
    assert not json_path.exists() and not junit_path.exists()


def test_release_junit_strict_coverage_is_blocking_only_when_enabled(tmp_path, capsys):
    manifest, build, sitemap = _fixture(tmp_path)
    (build / "unlisted.html").write_text("<html/>", encoding="utf-8")
    assert main([str(manifest), str(build), str(sitemap), "--junit"]) == 0
    assert ET.fromstring(capsys.readouterr().out).attrib["failures"] == "0"
    code = main([str(manifest), str(build), str(sitemap),
                 "--require-full-coverage", "--junit"])
    suite = ET.fromstring(capsys.readouterr().out)
    assert code == 1 and suite.attrib["failures"] == "1"
    assert "unlisted_html" in suite.find("testcase").attrib["name"]


def test_release_junit_xml_names_and_diagnostics_are_safe_and_deterministic():
    unusual = "a<>&\x01\ud800" + "x" * 900
    site = {
        "pages": [{"path": unusual, "checked_url": "https://example.test/",
                   "issues": [{"code": unusual, "expected": unusual, "actual": unusual}]}],
        "coverage": {"require_full_coverage": False, "unlisted": []},
    }
    sitemap = {"missing_required": ["https://example.test/"],
               "present_excluded": []}
    first = build_release_junit(site, sitemap)
    assert first == build_release_junit(site, sitemap)
    assert first.isascii() and "\x01" not in first
    suite = ET.fromstring(first)
    assert suite.attrib["failures"] == "2"
    assert all(len(case.attrib["name"]) <= 300 for case in suite.findall("testcase"))
