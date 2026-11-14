"""Portable, bounded Markdown review of local baseline/candidate regressions."""

import json
import socket

import pytest

from searcharis.build_compare import main


def _fixture(tmp_path, *, baseline_title="Old", candidate_title=""):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    baseline.mkdir()
    candidate.mkdir()
    for root, title in ((baseline, baseline_title), (candidate, candidate_title)):
        (root / "index.html").write_text(
            f"<html><head><title>{title}</title></head></html>", encoding="utf-8"
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "pages": [{
            "path": "index.html",
            "contract": {"url": "https://seo-ci.invalid/", "title_present": True},
        }],
    }), encoding="utf-8")
    return manifest, baseline, candidate


def _run(manifest, baseline, candidate, *options):
    return main([str(manifest), str(baseline), str(candidate), *options])


def test_markdown_file_reports_new_regression_without_network(tmp_path, monkeypatch, capsys):
    manifest, baseline, candidate = _fixture(tmp_path)
    destination = tmp_path / "summary.md"

    def deny_network(*_args, **_kwargs):
        raise AssertionError("offline comparison attempted network access")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    assert _run(manifest, baseline, candidate, "--markdown-file", str(destination)) == 1
    assert capsys.readouterr().out == ""
    raw = destination.read_bytes()
    assert not raw.startswith(b"\xff\xfe")
    text = raw.decode("utf-8")
    assert "**Result: REGRESSION**" in text
    assert "title_presence" in text
    assert "index.html" in text
    assert "New page issues: 1" in text
    assert "Inherited baseline issues: 0" in text
    assert "does not fetch" in text


def test_markdown_pass_does_not_block_preexisting_debt(tmp_path, capsys):
    manifest, baseline, candidate = _fixture(
        tmp_path, baseline_title="", candidate_title=""
    )
    destination = tmp_path / "known-debt.md"
    assert _run(manifest, baseline, candidate, "--markdown-file", str(destination)) == 0
    assert capsys.readouterr().out == ""
    report = destination.read_text(encoding="utf-8")
    assert "**Result: PASS**" in report
    assert "Inherited baseline issues: 1" in report
    assert "No new blocking findings" in report


@pytest.mark.parametrize("location", ["existing", "baseline", "candidate"])
def test_markdown_output_never_overwrites_or_modifies_build(tmp_path, capsys, location):
    manifest, baseline, candidate = _fixture(tmp_path)
    destination = {
        "existing": tmp_path / "existing.md",
        "baseline": baseline / "report.md",
        "candidate": candidate / "report.md",
    }[location]
    if location == "existing":
        destination.write_text("OWNER CONTENT", encoding="utf-8")
    assert _run(manifest, baseline, candidate, "--markdown-file", str(destination)) == 2
    assert "Invalid input" in capsys.readouterr().err
    if location == "existing":
        assert destination.read_text(encoding="utf-8") == "OWNER CONTENT"
    else:
        assert not destination.exists()


def test_invalid_input_creates_no_markdown(tmp_path, capsys):
    manifest, baseline, candidate = _fixture(tmp_path)
    report = tmp_path / "should-not-exist.md"
    assert _run(manifest, baseline, candidate / "missing", "--markdown-file", str(report)) == 2
    assert not report.exists()
    assert "Invalid input" in capsys.readouterr().err


def test_markdown_escapes_untrusted_findings_and_bounds_long_reports():
    from searcharis.comparison_markdown import build_comparison_markdown

    hostile = "<script>alert(1)</script>\n# FORGED"
    issue = {
        "path": hostile, "checked_url": "https://seo-ci.invalid/",
        "issue": {"code": hostile, "expected": "x" * 2000, "actual": False},
    }
    result = {
        "status": "regression",
        "summary": {
            "new_issues": 35, "existing_issues": 2, "resolved_issues": 0,
            "removed_pages": 0, "new_unlisted": 0, "new_unverified_targets": 0,
        },
        "new_issues": [issue] * 35, "removed_pages": [], "new_unlisted": [],
        "new_unverified_targets": [],
    }
    report = build_comparison_markdown(result)
    assert "<script>" not in report
    assert "&lt;script&gt;" in report
    assert "\n# FORGED" not in report
    assert "15 more" in report
    assert len(report) < 10_000


def test_bounded_markdown_preserves_one_actionable_example_from_each_category():
    from searcharis.comparison_markdown import build_comparison_markdown

    issues = [{
        "path": f"page-{i}.html",
        "issue": {"code": "title_presence", "expected": True, "actual": False},
    } for i in range(40)]
    result = {
        "status": "regression",
        "summary": {
            "new_issues": 40, "removed_pages": 1, "new_unlisted": 1,
            "new_unverified_targets": 1, "existing_issues": 0,
            "resolved_issues": 0,
        },
        "new_issues": issues,
        "removed_pages": [{"path": "removed-critical.html"}],
        "new_unlisted": ["new-unlisted.html"],
        "new_unverified_targets": [{
            "check": "canonical", "path": "index.html",
            "target": "https://example.invalid/unverified",
        }],
    }

    report = build_comparison_markdown(
        result, gate_new_unlisted=True, gate_new_unverified_targets=True
    )
    assert "removed-critical.html" in report
    assert "new-unlisted.html" in report
    assert "example.invalid/unverified" in report
    assert "23 more new observations" in report
    detail_lines = [line for line in report.splitlines() if line.startswith((
        "- Page ", "- Removed reviewed page:", "- New unlisted HTML",
        "- New unverified target",
    ))]
    assert len(detail_lines) == 20


def test_removed_page_flood_does_not_hide_new_unlisted_observations():
    from searcharis.comparison_markdown import build_comparison_markdown

    result = {
        "status": "regression",
        "summary": {
            "new_issues": 0, "removed_pages": 30, "new_unlisted": 1,
            "new_unverified_targets": 0, "existing_issues": 0,
            "resolved_issues": 0,
        },
        "new_issues": [],
        "removed_pages": [{"path": f"removed-{i}.html"} for i in range(30)],
        "new_unlisted": ["new-unlisted.html"],
        "new_unverified_targets": [],
    }
    report = build_comparison_markdown(result, gate_new_unlisted=True)
    assert "new-unlisted.html" in report
    assert "11 more new observations" in report


def test_unlisted_observations_keep_gate_distinction(tmp_path):
    manifest, baseline, candidate = _fixture(
        tmp_path, baseline_title="Old", candidate_title="Old"
    )
    (candidate / "extra.html").write_text("<title>Extra</title>", encoding="utf-8")
    optional_report = tmp_path / "optional.md"
    gated_report = tmp_path / "gated.md"
    assert _run(manifest, baseline, candidate, "--markdown-file", str(optional_report)) == 0
    assert "not gated" in optional_report.read_text(encoding="utf-8")
    assert _run(manifest, baseline, candidate, "--fail-on-new-unlisted",
                "--markdown-file", str(gated_report)) == 1
    assert "blocking" in gated_report.read_text(encoding="utf-8")


def test_markdown_report_mode_is_exclusive_with_json(tmp_path, capsys):
    manifest, baseline, candidate = _fixture(tmp_path)
    with pytest.raises(SystemExit) as failure:
        _run(manifest, baseline, candidate,
             "--json", "--markdown-file", str(tmp_path / "summary.md"))
    assert failure.value.code == 2
    assert "stdout report mode is not allowed with file report modes" in capsys.readouterr().err
