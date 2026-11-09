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


def test_comparison_rejects_weakened_contract_for_same_route(tmp_path):
    (tmp_path / "index.html").write_text("<html><head><title>OK</title></head></html>", encoding="utf-8")
    baseline_manifest = {
        "version": 1,
        "pages": [{"path": "index.html", "contract": {
            "url": "https://example.test/", "title_present": True, "h1_max": 0,
        }}],
    }
    candidate_manifest = {
        "version": 1,
        "pages": [{"path": "index.html", "contract": {
            "url": "https://example.test/", "h1_max": 0,
        }}],
    }
    baseline = verify_manifest(baseline_manifest, tmp_path)
    candidate = verify_manifest(candidate_manifest, tmp_path)
    assert baseline["status"] == candidate["status"] == "pass"

    with pytest.raises(ValueError, match="contract"):
        compare_reports(baseline, candidate)


def test_contract_fingerprints_ignore_json_key_order(tmp_path):
    (tmp_path / "index.html").write_text("<html><head><title>OK</title></head></html>", encoding="utf-8")
    contract_a = {"url": "https://example.test/", "title_present": True, "h1_max": 0}
    contract_b = {"h1_max": 0, "title_present": True, "url": "https://example.test/"}
    def manifest(contract):
        return {"version": 1, "pages": [{
            "path": "index.html", "contract": contract,
        }]}
    before = verify_manifest(manifest(contract_a), tmp_path)
    after = verify_manifest(manifest(contract_b), tmp_path)

    digest = before["pages"][0]["contract_sha256"]
    assert len(digest) == 64
    assert digest == after["pages"][0]["contract_sha256"]
    assert compare_reports(before, after)["status"] == "pass"


def test_comparison_rejects_mixed_or_invalid_contract_fingerprints():
    legacy = _report([("index.html", [])])
    fingerprinted = _report([("index.html", [])])
    fingerprinted["pages"][0]["contract_sha256"] = "a" * 64

    # Saved reports predating fingerprints remain readable as a pair.
    assert compare_reports(legacy, legacy)["status"] == "pass"
    with pytest.raises(ValueError, match="fingerprint formats"):
        compare_reports(legacy, fingerprinted)
    fingerprinted["pages"][0]["contract_sha256"] = "bad"
    with pytest.raises(ValueError, match="invalid contract fingerprint"):
        compare_reports(fingerprinted, fingerprinted)


def test_new_route_with_fingerprint_does_not_require_baseline_update(tmp_path):
    (tmp_path / "index.html").write_text("<html><head><title>OK</title></head></html>", encoding="utf-8")
    extra = tmp_path / "new.html"
    extra.write_text("<html><head><title>New</title></head></html>", encoding="utf-8")
    root = {"path": "index.html", "contract": {
        "url": "https://example.test/", "title_present": True,
    }}
    added = {"path": "new.html", "contract": {
        "url": "https://example.test/new", "title_present": True,
    }}
    before = verify_manifest({"version": 1, "pages": [root]}, tmp_path)
    after = verify_manifest({"version": 1, "pages": [root, added]}, tmp_path)

    assert compare_reports(before, after)["status"] == "pass"


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


def test_repeated_identical_findings_are_counted_as_new_regressions():
    baseline = _report([("en/index.html", [_issue()])])
    candidate = _report([("en/index.html", [_issue(), _issue()])])

    result = compare_reports(baseline, candidate)
    assert result["status"] == "regression"
    assert result["summary"]["new_issues"] == 1
    assert result["summary"]["existing_issues"] == 1
    assert len(result["new_issues"]) == 1
    assert compare_reports(candidate, baseline)["summary"]["resolved_issues"] == 1


def test_repeated_invalid_html_hreflang_links_are_not_hidden_in_comparison(tmp_path):
    html = tmp_path / "index.html"
    manifest = {
        "version": 1,
        "pages": [{
            "path": "index.html",
            "contract": {"url": "https://example.test/", "title_present": True},
        }],
    }
    invalid_link = '<link rel="alternate" hreflang="en" href="javascript:bad">'
    html.write_text(f"<html><head><title>OK</title>{invalid_link}</head></html>", encoding="utf-8")
    baseline = verify_manifest(manifest, tmp_path, check_hreflang_reciprocity=True)
    html.write_text(
        f"<html><head><title>OK</title>{invalid_link}{invalid_link}</head></html>",
        encoding="utf-8",
    )
    candidate = verify_manifest(manifest, tmp_path, check_hreflang_reciprocity=True)

    assert sum(issue["code"] == "hreflang_invalid_url:en" for issue in baseline["pages"][0]["issues"]) == 1
    assert sum(issue["code"] == "hreflang_invalid_url:en" for issue in candidate["pages"][0]["issues"]) == 2
    diff = compare_reports(baseline, candidate)
    assert diff["status"] == "regression"
    assert diff["summary"]["new_issues"] == 1
    assert diff["new_issues"][0]["issue"]["code"] == "hreflang_invalid_url:en"


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


def test_strict_comparison_accepts_case_distinct_unlisted_build_artifact():
    # On a case-sensitive build these are two different HTML files. The
    # manifest checker reports the second one as unlisted, not a duplicate.
    baseline = _report([("en/index.html", [])], strict=True)
    candidate = _report(
        [("en/index.html", [])], unlisted=("en/INDEX.HTML",), strict=True,
    )
    diff = compare_reports(baseline, candidate)
    assert diff["status"] == "regression"
    assert diff["new_unlisted"] == ["en/INDEX.HTML"]
    assert compare_reports(candidate, candidate)["status"] == "pass"


def test_comparison_accepts_two_explicitly_checked_case_distinct_routes():
    names = [("en/index.html", []), ("en/INDEX.HTML", [])]
    baseline = _report(names, strict=True)
    candidate = _report([(names[0][0], []), (names[1][0], [_issue()])], strict=True)

    result = compare_reports(baseline, candidate)
    assert result["status"] == "regression"
    assert result["summary"]["new_issues"] == 1
    assert result["summary"]["removed_pages"] == 0
    assert result["new_issues"][0]["path"] == "en/INDEX.HTML"
    assert compare_reports(baseline, baseline)["status"] == "pass"


def test_comparison_counts_case_distinct_unlisted_artifacts_separately():
    baseline = _report([("index.html", [])], strict=True)
    candidate = _report(
        [("index.html", [])], unlisted=("en/index.html", "en/INDEX.HTML"), strict=True,
    )
    diff = compare_reports(baseline, candidate)
    assert diff["status"] == "regression"
    assert diff["summary"]["new_unlisted"] == 2


def test_cli_handles_case_distinct_unlisted_artifact_as_regression(tmp_path, capsys):
    baseline = _report([("en/index.html", [])], strict=True)
    candidate = _report(
        [("en/index.html", [])], unlisted=("en/INDEX.HTML",), strict=True,
    )
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text(json.dumps(baseline), encoding="utf-8")
    after.write_text(json.dumps(candidate), encoding="utf-8")

    assert main([str(before), str(after), "--json"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "regression"
    assert output["new_unlisted"] == ["en/INDEX.HTML"]


@pytest.mark.parametrize("unlisted", [("index.html",), ("extra.html", "extra.html")])
def test_comparison_still_rejects_exact_duplicate_coverage_paths(unlisted):
    report = _report([("index.html", [])], unlisted=unlisted)
    with pytest.raises(ValueError, match="coverage|unlisted"):
        compare_reports(report, report)


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


@pytest.mark.parametrize("nonfinite", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_cli_rejects_nonfinite_values_in_local_reports(tmp_path, capsys, nonfinite):
    report = _report([("en/index.html", [_issue()])])
    raw = json.dumps(report).replace('"actual": "missing"', f'"actual": {nonfinite}')
    before = tmp_path / "baseline.json"
    after = tmp_path / "candidate.json"
    before.write_text(raw, encoding="utf-8")
    after.write_text(raw, encoding="utf-8")

    assert main([str(before), str(after), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_compare_reports_rejects_nonfinite_python_values_but_accepts_finite_numbers():
    report = _report([("en/index.html", [_issue(1.25)])])
    assert compare_reports(report, report)["status"] == "pass"
    report["pages"][0]["issues"][0]["actual"] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        compare_reports(report, report)


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


@pytest.mark.parametrize("json_output", [True, False])
def test_cli_rejects_excessively_nested_local_report_without_traceback(
    tmp_path, capsys, json_output,
):
    before = tmp_path / "baseline.json"
    after = tmp_path / "candidate.json"
    before.write_text(json.dumps(_report([("index.html", [])])), encoding="utf-8")
    # This small syntactically nested input can exhaust the JSON decoder's
    # recursion budget; it must be an input error, not a CLI traceback.
    after.write_text("[" * 5000 + "0" + "]" * 5000, encoding="utf-8")

    args = [str(before), str(after)] + (["--json"] if json_output else [])
    assert main(args) == 2
    output = capsys.readouterr()
    if json_output:
        assert json.loads(output.out)["status"] == "error"
        assert output.err == ""
    else:
        assert "Invalid or incompatible report" in output.err
        assert "Traceback" not in output.err


def test_comparison_wraps_encoder_recursion_as_invalid_report(monkeypatch):
    from searcharis import report_compare

    report = _report([("index.html", [_issue()])])
    original_dumps = report_compare.json.dumps

    def simulate_encoder_depth_limit(*args, **kwargs):
        if kwargs.get("allow_nan") is False:
            raise RecursionError("encoder nesting limit")
        return original_dumps(*args, **kwargs)

    # Python versions differ in how deeply their C JSON encoder can recurse.
    # Exercise the catch path directly rather than depending on that limit.
    monkeypatch.setattr(report_compare.json, "dumps", simulate_encoder_depth_limit)

    with pytest.raises(ValueError, match="invalid JSON values"):
        compare_reports(report, report)
