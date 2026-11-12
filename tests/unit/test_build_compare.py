"""One-command, local-only baseline/candidate SEO build comparisons."""

import json
import os
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from searcharis.report_compare import compare_reports
from searcharis.site_ci import verify_manifest


def _fixture(tmp_path, *, baseline_title="Old", candidate_title="Old"):
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    (baseline / "index.html").write_text(
        f"<html><head><title>{baseline_title}</title></head></html>", encoding="utf-8"
    )
    (candidate / "index.html").write_text(
        f"<html><head><title>{candidate_title}</title></head></html>", encoding="utf-8"
    )
    manifest = {
        "version": 1,
        "pages": [{
            "path": "index.html",
            "contract": {"url": "https://example.test/", "title_present": True},
        }],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, baseline, candidate, manifest


def _run(manifest, baseline, candidate, *options):
    from searcharis.build_compare import main

    return main([str(manifest), str(baseline), str(candidate), *options])


def test_persistent_preexisting_failure_does_not_block(tmp_path, capsys):
    manifest, baseline, candidate, _ = _fixture(
        tmp_path, baseline_title="", candidate_title=""
    )
    assert _run(manifest, baseline, candidate) == 0
    stdout = capsys.readouterr().out
    assert "PASS 0 new issues, 1 existing" in stdout


def test_new_failure_blocks_with_actionable_human_output(tmp_path, capsys):
    manifest, baseline, candidate, _ = _fixture(
        tmp_path, baseline_title="OK", candidate_title=""
    )
    assert _run(manifest, baseline, candidate) == 1
    stdout = capsys.readouterr().out
    assert "REGRESSION 1 new issues" in stdout
    assert "NEW index.html" in stdout
    assert "title_presence" in stdout


def test_json_matches_existing_two_report_comparison(tmp_path, capsys):
    manifest, baseline, candidate, data = _fixture(
        tmp_path, baseline_title="", candidate_title="Correct"
    )
    assert _run(manifest, baseline, candidate, "--json") == 0
    actual = json.loads(capsys.readouterr().out)
    expected = compare_reports(
        verify_manifest(data, baseline), verify_manifest(data, candidate)
    )
    assert actual == expected
    assert actual["summary"]["resolved_issues"] == 1


def test_new_unlisted_page_opt_in_and_full_coverage(tmp_path, capsys):
    manifest, baseline, candidate, _ = _fixture(tmp_path)
    (candidate / "extra.html").write_text("<title>Extra</title>", encoding="utf-8")
    assert _run(manifest, baseline, candidate) == 0
    assert _run(manifest, baseline, candidate, "--fail-on-new-unlisted") == 1
    assert _run(manifest, baseline, candidate, "--require-full-coverage") == 1
    assert "UNLISTED extra.html" in capsys.readouterr().out


def test_unverified_target_gate_requires_cross_page_check(tmp_path, capsys):
    manifest, baseline, candidate, _ = _fixture(tmp_path)
    assert _run(
        manifest, baseline, candidate, "--fail-on-new-unverified-targets"
    ) == 2
    assert "requires at least one cross-page check" in capsys.readouterr().err


def test_new_unverified_canonical_target_can_block(tmp_path, capsys):
    manifest, baseline, candidate, _ = _fixture(tmp_path)
    (candidate / "index.html").write_text(
        "<html><head><title>Old</title>"
        '<link rel="canonical" href="https://outside.test/"></head></html>',
        encoding="utf-8",
    )
    assert _run(
        manifest, baseline, candidate, "--check-canonical-consistency",
        "--fail-on-new-unverified-targets",
    ) == 1
    assert "UNVERIFIED 1 new targets" in capsys.readouterr().out


def test_invalid_input_fails_closed_with_json_error(tmp_path, capsys):
    manifest, baseline, candidate, _ = _fixture(tmp_path)
    assert _run(manifest, baseline, candidate / "absent", "--json") == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert "error" in result


def test_same_build_directory_is_not_a_valid_baseline(tmp_path, capsys):
    manifest, baseline, _candidate, _ = _fixture(tmp_path)
    assert _run(manifest, baseline, baseline) == 2
    assert "different build directories" in capsys.readouterr().err


@pytest.mark.parametrize("baseline_is_parent", [True, False])
def test_nested_build_roots_are_rejected_before_comparison(
    tmp_path, capsys, baseline_is_parent
):
    manifest, baseline, candidate, _ = _fixture(tmp_path)
    if baseline_is_parent:
        nested = baseline / "nested-candidate"
        candidate = nested
    else:
        nested = candidate / "nested-baseline"
        baseline = nested
    nested.mkdir()
    (nested / "index.html").write_text(
        "<html><head><title>Old</title></head></html>", encoding="utf-8"
    )

    assert _run(manifest, baseline, candidate, "--json") == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert "overlapping build directories" in result["error"]


def test_hardlinked_build_html_cannot_masquerade_as_independent_baseline(tmp_path, capsys):
    manifest, baseline, candidate, _ = _fixture(tmp_path)
    (candidate / "index.html").unlink()
    try:
        os.link(baseline / "index.html", candidate / "index.html")
    except OSError as error:
        pytest.skip(f"Filesystem does not support hard links: {error}")

    # A later candidate write silently mutates the baseline as well. Without
    # shared-file validation, the comparator reports a false PASS.
    (candidate / "index.html").write_text(
        "<html><head></head></html>", encoding="utf-8"
    )
    assert (baseline / "index.html").read_text(encoding="utf-8") == (
        candidate / "index.html"
    ).read_text(encoding="utf-8")

    assert _run(manifest, baseline, candidate, "--json") == 2
    result = json.loads(capsys.readouterr().out)
    assert "share a hard-linked HTML artifact" in result["error"]


def test_hardlinked_html_under_different_page_names_is_rejected(tmp_path, capsys):
    manifest, baseline, candidate, data = _fixture(tmp_path)
    data["pages"].append({
        "path": "about.html",
        "contract": {"url": "https://example.test/about", "title_present": True},
    })
    manifest.write_text(json.dumps(data), encoding="utf-8")
    for root in (baseline, candidate):
        (root / "about.html").write_text("<title>About</title>", encoding="utf-8")
    (candidate / "index.html").unlink()
    try:
        os.link(baseline / "about.html", candidate / "index.html")
    except OSError as error:
        pytest.skip(f"Filesystem does not support hard links: {error}")

    assert _run(manifest, baseline, candidate, "--json") == 2
    assert "about.html and index.html" in json.loads(capsys.readouterr().out)["error"]


def test_comparison_never_uses_network(tmp_path, monkeypatch, capsys):
    manifest, baseline, candidate, _ = _fixture(tmp_path)

    def reject_network(*_args, **_kwargs):
        raise AssertionError("offline SEO build comparison attempted a connection")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    assert _run(manifest, baseline, candidate, "--json") == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


def test_python_module_entrypoint_accepts_two_builds(tmp_path):
    manifest, baseline, candidate, _ = _fixture(tmp_path)
    invocation = subprocess.run(
        [
            sys.executable, "-m", "searcharis.build_compare",
            str(manifest), str(baseline), str(candidate), "--json",
        ],
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert invocation.returncode == 0
    assert invocation.stderr == ""
    assert json.loads(invocation.stdout)["status"] == "pass"


def test_sarif_reports_only_new_page_failures_not_existing_ones(tmp_path, capsys):
    manifest, baseline, candidate, data = _fixture(
        tmp_path, baseline_title="", candidate_title=""
    )
    data["pages"].append({
        "path": "about.html",
        "contract": {"url": "https://example.test/about", "title_present": True},
    })
    manifest.write_text(json.dumps(data), encoding="utf-8")
    (baseline / "about.html").write_text(
        "<html><head><title>About</title></head></html>", encoding="utf-8"
    )
    (candidate / "about.html").write_text(
        "<html><head></head></html>", encoding="utf-8"
    )

    assert _run(manifest, baseline, candidate, "--sarif") == 1
    report = json.loads(capsys.readouterr().out)
    assert report["version"] == "2.1.0"
    results = report["runs"][0]["results"]
    assert len(results) == 1
    assert results[0]["ruleId"] == "title_presence"
    assert results[0]["locations"][0]["physicalLocation"]["artifactLocation"][
        "uri"
    ] == "about.html"
    assert results[0]["partialFingerprints"]["searcharis/issue/v1"]


def test_sarif_no_new_regressions_has_empty_results_even_with_known_defects(tmp_path, capsys):
    manifest, baseline, candidate, _ = _fixture(
        tmp_path, baseline_title="", candidate_title=""
    )
    assert _run(manifest, baseline, candidate, "--sarif") == 0
    run = json.loads(capsys.readouterr().out)["runs"][0]
    assert run["results"] == []
    assert run["tool"]["driver"]["rules"] == []


def test_sarif_includes_only_gated_new_unlisted_html(tmp_path, capsys):
    manifest, baseline, candidate, _ = _fixture(tmp_path)
    (candidate / "new #route.html").write_text("<title>Test</title>", encoding="utf-8")
    assert _run(manifest, baseline, candidate, "--sarif") == 0
    assert json.loads(capsys.readouterr().out)["runs"][0]["results"] == []
    for flag in ("--fail-on-new-unlisted", "--require-full-coverage"):
        assert _run(manifest, baseline, candidate, "--sarif", flag) == 1
        results = json.loads(capsys.readouterr().out)["runs"][0]["results"]
        assert len(results) == 1
        assert results[0]["ruleId"] == "unlisted_html"
        assert results[0]["locations"][0]["physicalLocation"]["artifactLocation"][
            "uri"
        ] == "new%20%23route.html"


def test_sarif_new_unverified_target_is_included_only_when_gated(tmp_path, capsys):
    manifest, baseline, candidate, _ = _fixture(tmp_path)
    (candidate / "index.html").write_text(
        '<html><head><title>Old</title>'
        '<link rel="canonical" href="https://external.test/"></head></html>',
        encoding="utf-8",
    )
    flags = ("--sarif", "--check-canonical-consistency")
    assert _run(manifest, baseline, candidate, *flags) == 0
    assert json.loads(capsys.readouterr().out)["runs"][0]["results"] == []
    assert _run(manifest, baseline, candidate, *flags, "--fail-on-new-unverified-targets") == 1
    result = json.loads(capsys.readouterr().out)["runs"][0]["results"]
    assert len(result) == 1
    assert result[0]["ruleId"] == "canonical_consistency_unverified_target"
    assert "https://external.test/" in result[0]["message"]["text"]


def test_sarif_source_map_links_reviewed_existing_source(tmp_path, capsys, monkeypatch):
    manifest, baseline, candidate, _ = _fixture(
        tmp_path, baseline_title="OK", candidate_title=""
    )
    source = tmp_path / "src" / "page.astro"
    source.parent.mkdir()
    source.write_text("<title>Source</title>", encoding="utf-8")
    mapping = tmp_path / "source-map.json"
    mapping.write_text(json.dumps({
        "version": 1, "sources": {"index.html": "src/page.astro"}
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _run(manifest, baseline, candidate, "--sarif", "--sarif-source-map", str(mapping)) == 1
    finding = json.loads(capsys.readouterr().out)["runs"][0]["results"][0]
    location = finding["locations"][0]["physicalLocation"]["artifactLocation"]
    assert location == {"uri": "src/page.astro"}
    assert finding["relatedLocations"][0]["physicalLocation"]["artifactLocation"][
        "uri"
    ] == "index.html"


def test_sarif_source_map_rejects_unknown_or_escaping_paths(tmp_path, capsys, monkeypatch):
    manifest, baseline, candidate, _ = _fixture(tmp_path)
    mapping = tmp_path / "source-map.json"
    mapping.write_text(json.dumps({
        "version": 1, "sources": {"absent.html": "../outside.py"}
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _run(manifest, baseline, candidate, "--sarif", "--sarif-source-map", str(mapping)) == 2
    assert "Invalid input" in capsys.readouterr().err


def test_sarif_and_json_outputs_are_mutually_exclusive(tmp_path):
    manifest, baseline, candidate, _ = _fixture(tmp_path)
    with pytest.raises(SystemExit) as error:
        _run(manifest, baseline, candidate, "--sarif", "--json")
    assert error.value.code == 2


def test_junit_reports_only_new_blocking_regressions(tmp_path, capsys):
    manifest, baseline, candidate, data = _fixture(
        tmp_path, baseline_title="", candidate_title=""
    )
    data["pages"].append({
        "path": "about.html",
        "contract": {"url": "https://example.test/about", "title_present": True},
    })
    manifest.write_text(json.dumps(data), encoding="utf-8")
    (baseline / "about.html").write_text(
        "<html><head><title>About</title></head></html>", encoding="utf-8"
    )
    (candidate / "about.html").write_text("<html><head></head></html>", encoding="utf-8")

    assert _run(manifest, baseline, candidate, "--junit") == 1
    suite = ET.fromstring(capsys.readouterr().out)
    assert suite.tag == "testsuite"
    assert suite.attrib["tests"] == "1"
    assert suite.attrib["failures"] == "1"
    cases = suite.findall("testcase")
    assert len(cases) == 1
    assert "about.html" in cases[0].attrib["name"]
    failure = cases[0].find("failure")
    assert failure is not None
    assert "title_presence" in failure.attrib["message"]
    assert "index.html" not in ET.tostring(suite, encoding="unicode")


def test_junit_emits_one_successful_check_for_tolerated_baseline_debt(tmp_path, capsys):
    manifest, baseline, candidate, _ = _fixture(
        tmp_path, baseline_title="", candidate_title=""
    )
    assert _run(manifest, baseline, candidate, "--junit") == 0
    suite = ET.fromstring(capsys.readouterr().out)
    assert suite.attrib["tests"] == "1"
    assert suite.attrib["failures"] == "0"
    assert suite.find("testcase") is not None
    assert suite.find("testcase/failure") is None


def test_junit_unlisted_and_unverified_targets_obey_gates(tmp_path, capsys):
    manifest, baseline, candidate, _ = _fixture(tmp_path)
    (candidate / "extra&route.html").write_text("<title>Extra</title>", encoding="utf-8")
    assert _run(manifest, baseline, candidate, "--junit") == 0
    assert ET.fromstring(capsys.readouterr().out).attrib["failures"] == "0"
    assert _run(manifest, baseline, candidate, "--junit", "--fail-on-new-unlisted") == 1
    suite = ET.fromstring(capsys.readouterr().out)
    assert suite.attrib["failures"] == "1"
    assert "extra&route.html" in suite.find("testcase").attrib["name"]

    (candidate / "index.html").write_text(
        '<html><head><title>Old</title>'
        '<link rel="canonical" href="https://outside.test/"></head></html>',
        encoding="utf-8",
    )
    assert _run(manifest, baseline, candidate, "--junit", "--check-canonical-consistency") == 0
    assert ET.fromstring(capsys.readouterr().out).attrib["failures"] == "0"
    assert _run(
        manifest, baseline, candidate, "--junit", "--check-canonical-consistency",
        "--fail-on-new-unverified-targets",
    ) == 1
    suite = ET.fromstring(capsys.readouterr().out)
    assert suite.attrib["failures"] == "1"
    assert "canonical_consistency_unverified_target" in suite.find("testcase/failure").attrib["message"]


@pytest.mark.parametrize("conflicting_flag", ["--sarif", "--json"])
def test_junit_output_is_exclusive(tmp_path, conflicting_flag):
    manifest, baseline, candidate, _ = _fixture(tmp_path)
    with pytest.raises(SystemExit) as error:
        _run(manifest, baseline, candidate, "--junit", conflicting_flag)
    assert error.value.code == 2


def test_junit_bad_input_has_no_malformed_xml_success_report(tmp_path, capsys):
    manifest, baseline, candidate, _ = _fixture(tmp_path)
    assert _run(manifest, baseline, candidate / "missing", "--junit") == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "Invalid input" in output.err
