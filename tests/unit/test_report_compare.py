"""Baselined, offline comparison of deterministic SEO build reports."""

import json
import socket

import pytest

from searcharis.report_compare import compare_reports, main
from searcharis.site_ci import verify_manifest


def _issue(actual="missing"):
    return {"code": "title_presence", "expected": True, "actual": actual}


def _report(pages, *, unlisted=(), strict=False, checks=()):
    items = [
        {
            "path": path,
            "checked_url": f"https://example.test/{path}",
            "status": "fail" if issues else "pass",
            "issues": issues,
        }
        for path, issues in pages
    ]
    failed = sum(bool(page["issues"]) for page in items)
    report = {
        "status": "fail" if failed or (strict and unlisted) else "pass",
        "summary": {
            "checked": len(items), "passed": len(items) - failed, "failed": failed,
        },
        "coverage": {
            "discovered": len(items) + len(unlisted),
            "checked": len(items),
            "unlisted": list(unlisted),
            "require_full_coverage": strict,
        },
        "pages": items,
    }
    for check in checks:
        report[check] = {
            "internal_targets_checked": 0, "unverified_targets": [],
        } if check == "canonical_consistency" else {
            "checked_pairs": 0, "unverified_targets": [],
        }
    return report


def test_persistent_existing_failure_does_not_block_candidate():
    baseline = _report([("en/index.html", [_issue()])])
    diff = compare_reports(baseline, _report([("en/index.html", [_issue()])]))
    assert diff["status"] == "pass"
    assert diff["summary"]["existing_issues"] == 1
    assert diff["new_issues"] == []


def test_new_and_resolved_failures_have_stable_route_scoped_detail():
    baseline = _report([("sl/index.html", [_issue()]), ("en/index.html", [])])
    candidate = _report([("en/index.html", [_issue()]), ("sl/index.html", [])])
    diff = compare_reports(baseline, candidate)
    assert diff["status"] == "regression"
    assert diff["summary"]["new_issues"] == 1
    assert diff["summary"]["resolved_issues"] == 1
    assert diff["new_issues"][0]["path"] == "en/index.html"
    assert diff["resolved_issues"][0]["path"] == "sl/index.html"


def test_changed_observed_value_is_a_new_failure_not_silently_existing():
    baseline = _report([("en/index.html", [_issue("old")])])
    candidate = _report([("en/index.html", [_issue("new")])])
    diff = compare_reports(baseline, candidate)
    assert diff["summary"]["new_issues"] == 1
    assert diff["summary"]["resolved_issues"] == 1


def test_missing_or_changed_baseline_page_cannot_hide_an_existing_failure():
    baseline = _report([("en/index.html", [_issue()]), ("sl/index.html", [])])
    for candidate in (
        _report([("sl/index.html", [])]),
        _report([("other/index.html", []), ("sl/index.html", [])]),
    ):
        diff = compare_reports(baseline, candidate)
        assert diff["status"] == "regression"
        assert len(diff["removed_pages"]) == 1
        assert diff["removed_pages"][0]["path"] == "en/index.html"


def test_strict_new_unlisted_artifact_blocks_only_when_new():
    baseline = _report([("en/index.html", [])], unlisted=("legacy.html",), strict=True)
    same = compare_reports(baseline, _report(
        [("en/index.html", [])], unlisted=("legacy.html",), strict=True,
    ))
    assert same["status"] == "pass"
    new = compare_reports(baseline, _report(
        [("en/index.html", [])], unlisted=("legacy.html", "extra.html"), strict=True,
    ))
    assert new["status"] == "regression"
    assert new["new_unlisted"] == ["extra.html"]


def test_different_check_options_are_not_comparable():
    baseline = _report([("en/index.html", [])])
    changed = _report([("en/index.html", [])], checks=("hreflang_reciprocity",))
    with pytest.raises(ValueError, match="options"):
        compare_reports(baseline, changed)
    with pytest.raises(ValueError, match="options"):
        compare_reports(baseline, _report([("en/index.html", [])], strict=True))


@pytest.mark.parametrize("corruption", ["counts", "status", "issues", "duplicate", "coverage"])
def test_inconsistent_or_forged_reports_fail_closed(corruption):
    report = _report([("en/index.html", [_issue()])])
    if corruption == "counts":
        report["summary"]["failed"] = 0
    elif corruption == "status":
        report["status"] = "pass"
    elif corruption == "issues":
        report["pages"][0]["issues"] = [{"expected": True, "actual": False}]
    elif corruption == "coverage":
        report["coverage"]["discovered"] = 42
    else:
        report["pages"].append(report["pages"][0])
        report["summary"]["checked"] = 2
        report["summary"]["failed"] = 2
        report["coverage"]["checked"] = 2
        report["coverage"]["discovered"] = 2
    with pytest.raises(ValueError):
        compare_reports(report, report)


def test_new_unverified_target_is_visible_but_not_an_asserted_failure():
    baseline = _report([("en/index.html", [])], checks=("canonical_consistency",))
    candidate = _report([("en/index.html", [])], checks=("canonical_consistency",))
    candidate["canonical_consistency"]["unverified_targets"] = [
        {"path": "en/index.html", "target": "https://outside.test/"}
    ]
    diff = compare_reports(baseline, candidate)
    assert diff["status"] == "pass"
    assert diff["summary"]["new_unverified_targets"] == 1


def test_cli_regression_exit_codes_and_offline_behavior(tmp_path, capsys, monkeypatch):
    baseline = _report([("en/index.html", [])])
    candidate = _report([("en/index.html", [_issue()])])
    before = tmp_path / "baseline.json"
    after = tmp_path / "candidate.json"
    before.write_text(json.dumps(baseline), encoding="utf-8")
    after.write_text(json.dumps(candidate), encoding="utf-8")

    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: (
        pytest.fail("Offline comparator must not use sockets")
    ))
    assert main([str(before), str(after), "--json"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "regression"
    assert output["summary"]["new_issues"] == 1

    after.write_text(json.dumps(baseline), encoding="utf-8")
    assert main([str(before), str(after)]) == 0
    assert "PASS" in capsys.readouterr().out

    after.write_text("{", encoding="utf-8")
    assert main([str(before), str(after), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_comparison_tracks_actual_local_html_build_regression(tmp_path):
    html = tmp_path / "index.html"
    manifest = {
        "version": 1,
        "pages": [{
            "path": "index.html",
            "contract": {"url": "https://example.test/", "title_present": True},
        }],
    }
    html.write_text("<html><head><title>Good</title></head><body></body></html>", encoding="utf-8")
    baseline = verify_manifest(manifest, tmp_path)
    html.write_text("<html><head></head><body></body></html>", encoding="utf-8")
    candidate = verify_manifest(manifest, tmp_path)
    result = compare_reports(baseline, candidate)
    assert result["status"] == "regression"
    assert result["new_issues"][0]["issue"]["code"] == "title_presence"
    assert result["summary"]["new_issues"] == 1
