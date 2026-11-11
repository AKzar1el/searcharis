"""One-command, local-only baseline/candidate SEO build comparisons."""

import json
import socket
import subprocess
import sys

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
