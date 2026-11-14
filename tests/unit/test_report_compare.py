"""Baselined, offline comparison of deterministic SEO build reports."""

import json
import socket
from pathlib import Path

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
    # Promoting a previously unlisted page into the approved manifest is safe.
    assert compare_reports(before, after, fail_on_new_unlisted=True)["status"] == "pass"


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


def test_opt_in_new_unlisted_gate_allows_legacy_coverage_debt():
    baseline = _report([("en/index.html", [])], unlisted=("legacy.html",))
    candidate = _report(
        [("en/index.html", [])], unlisted=("legacy.html", "new.html"),
    )

    # The existing non-strict baseline policy remains opt-in.
    assert compare_reports(baseline, candidate)["status"] == "pass"
    gated = compare_reports(baseline, candidate, fail_on_new_unlisted=True)
    assert gated["status"] == "regression"
    assert gated["summary"]["new_unlisted"] == 1
    assert gated["new_unlisted"] == ["new.html"]
    assert compare_reports(candidate, candidate, fail_on_new_unlisted=True)["status"] == "pass"
    assert compare_reports(candidate, baseline, fail_on_new_unlisted=True)["status"] == "pass"


@pytest.mark.parametrize("json_output", [True, False])
def test_cli_new_unlisted_gate_checks_actual_local_build(tmp_path, capsys, monkeypatch, json_output):
    root = tmp_path / "build"
    root.mkdir()
    (root / "index.html").write_text("<html><head><title>OK</title></head></html>", encoding="utf-8")
    (root / "legacy.html").write_text("<html><head></head></html>", encoding="utf-8")
    manifest = {"version": 1, "pages": [{
        "path": "index.html",
        "contract": {"url": "https://example.test/", "title_present": True},
    }]}
    baseline = verify_manifest(manifest, root)
    (root / "new.html").write_text("<html><head></head></html>", encoding="utf-8")
    candidate = verify_manifest(manifest, root)
    assert baseline["status"] == candidate["status"] == "pass"

    before, after = tmp_path / "baseline.json", tmp_path / "candidate.json"
    before.write_text(json.dumps(baseline), encoding="utf-8")
    after.write_text(json.dumps(candidate), encoding="utf-8")
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: (
        pytest.fail("Offline comparison must not fetch URLs")
    ))
    args = [str(before), str(after)] + (["--json"] if json_output else [])
    assert main(args) == 0
    capsys.readouterr()
    assert main(args + ["--fail-on-new-unlisted"]) == 1
    output = capsys.readouterr().out
    if json_output:
        result = json.loads(output)
        assert result["status"] == "regression"
        assert result["new_unlisted"] == ["new.html"]
        assert result["summary"]["new_unlisted"] == 1
    else:
        assert "REGRESSION" in output
        assert "UNLISTED new.html" in output


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


@pytest.mark.parametrize("alias", [
    "https://EXAMPLE.test/pricing",
    "https://example.test:443/pricing",
    "https://example.test/a/../pricing",
    "https://example.test/%70ricing",
    "https://example.test/pricing#section",
])
def test_archived_reports_reject_equivalent_page_url_identities(alias):
    report = _report([("one.html", []), ("two.html", [])])
    report["pages"][0]["checked_url"] = "https://example.test/pricing"
    report["pages"][1]["checked_url"] = alias
    with pytest.raises(ValueError, match="duplicate page path or URL"):
        compare_reports(report, report)


@pytest.mark.parametrize("different", [
    "https://example.test/Pricing",
    "https://example.test/pricing?mode=other",
    "https://example.test:444/pricing",
    "https://example.test/pric%2Fing",
    "http://example.test/pricing",
])
def test_archived_reports_preserve_distinct_page_url_identities(different):
    report = _report([("one.html", []), ("two.html", [])])
    report["pages"][0]["checked_url"] = "https://example.test/pricing"
    report["pages"][1]["checked_url"] = different
    assert compare_reports(report, report)["status"] == "pass"


def test_cli_rejects_archived_aliases_in_machine_readable_comparison(tmp_path, capsys):
    report = _report([("one.html", []), ("two.html", [])])
    report["pages"][0]["checked_url"] = "https://example.test/"
    report["pages"][1]["checked_url"] = "https://EXAMPLE.test:443"
    baseline, candidate = tmp_path / "base.json", tmp_path / "candidate.json"
    baseline.write_text(json.dumps(report), encoding="utf-8")
    candidate.write_text(json.dumps(report), encoding="utf-8")
    assert main([str(baseline), str(candidate), "--json"]) == 2
    assert "duplicate page path or URL" in json.loads(capsys.readouterr().out)["error"]


def test_different_check_options_are_not_comparable():
    baseline = _report([("en/index.html", [])])
    changed = _report([("en/index.html", [])], checks=("hreflang_reciprocity",))
    with pytest.raises(ValueError, match="options"):
        compare_reports(baseline, changed)
    with pytest.raises(ValueError, match="options"):
        compare_reports(baseline, _report([("en/index.html", [])], strict=True))


def test_strict_unverified_gate_rejects_reports_with_no_cross_page_checks():
    report = _report([("en/index.html", [])])
    assert compare_reports(report, report)["status"] == "pass"
    with pytest.raises(ValueError, match="requires.*cross-page check"):
        compare_reports(report, report, fail_on_new_unverified_targets=True)


@pytest.mark.parametrize("check,count_key", [
    ("canonical_consistency", "internal_targets_checked"),
    ("hreflang_reciprocity", "checked_pairs"),
    ("hreflang_clusters", "checked_pairs"),
])
@pytest.mark.parametrize("bad_count", [None, True, -1, "3"])
def test_comparison_rejects_corrupt_cross_page_check_counts(check, count_key, bad_count):
    report = _report([("en/index.html", [])], checks=(check,))
    report[check][count_key] = bad_count
    with pytest.raises(ValueError, match=f"invalid {check} data"):
        compare_reports(report, report, fail_on_new_unverified_targets=True)


@pytest.mark.parametrize("check,count_key", [
    ("canonical_consistency", "internal_targets_checked"),
    ("hreflang_reciprocity", "checked_pairs"),
    ("hreflang_clusters", "checked_pairs"),
])
def test_comparison_rejects_impossible_cross_page_check_counts(check, count_key):
    report = _report(
        [("en/index.html", []), ("de/index.html", [])], checks=(check,),
    )
    # Canonical has at most one internal target per source; each hreflang
    # check counts each directed peer pair at most once.
    report[check][count_key] = 3
    with pytest.raises(ValueError, match=f"invalid {check} data"):
        compare_reports(report, report)


def test_comparison_rejects_impossible_canonical_internal_plus_external_total():
    report = _report([("en/index.html", [])], checks=("canonical_consistency",))
    report["canonical_consistency"]["internal_targets_checked"] = 1
    report["canonical_consistency"]["unverified_targets"] = [{
        "path": "en/index.html", "target": "https://elsewhere.test/",
    }]
    with pytest.raises(ValueError, match="invalid canonical_consistency data"):
        compare_reports(report, report)


def test_comparison_accepts_real_fixture_maximum_hreflang_pair_counts():
    fixtures = (Path(__file__).resolve().parents[2] /
                "examples/seo-contract/generator-fixtures/astro")
    manifest = json.loads((fixtures / "manifest.json").read_text(encoding="utf-8"))
    report = verify_manifest(
        manifest, fixtures / "artifacts", require_full_coverage=True,
        check_hreflang_reciprocity=True, check_hreflang_clusters=True,
        check_canonical_consistency=True,
    )
    assert report["hreflang_clusters"]["checked_pairs"] == 2
    assert report["hreflang_reciprocity"]["checked_pairs"] == 2
    assert compare_reports(report, report, fail_on_new_unverified_targets=True)["status"] == "pass"


@pytest.mark.parametrize("json_output", [True, False])
def test_cli_rejects_impossible_cross_page_counts_as_input_error(tmp_path, capsys, json_output):
    report = _report([("index.html", [])], checks=("hreflang_clusters",))
    report["hreflang_clusters"]["checked_pairs"] = 1
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps(report), encoding="utf-8")
    after.write_text(json.dumps(report), encoding="utf-8")

    args = [str(before), str(after)] + (["--json"] if json_output else [])
    assert main(args) == 2
    output = capsys.readouterr()
    if json_output:
        assert json.loads(output.out)["status"] == "error"
    else:
        assert "hreflang_clusters" in output.err
        assert not output.out


@pytest.mark.parametrize("check", [
    "canonical_consistency", "hreflang_reciprocity", "hreflang_clusters",
])
def test_comparison_rejects_unverified_target_from_unknown_page(check):
    report = _report([("en/index.html", [])], checks=(check,))
    target = {"path": "ghost.html", "target": "https://outside.test/"}
    if check == "hreflang_clusters":
        target["language"] = "de"
    report[check]["unverified_targets"] = [target]
    with pytest.raises(ValueError, match=f"invalid {check} unverified targets"):
        compare_reports(report, report, fail_on_new_unverified_targets=True)


@pytest.mark.parametrize("check", [
    "canonical_consistency", "hreflang_reciprocity", "hreflang_clusters",
])
def test_comparison_rejects_falsely_unverified_manifest_target(check):
    report = _report(
        [("en/index.html", []), ("de/index.html", [])], checks=(check,),
    )
    target = {
        "path": "en/index.html",
        "target": "https://example.test/de/index.html",
    }
    if check == "hreflang_clusters":
        target["language"] = "de"
    # The generator only records outside-manifest destinations here. A target
    # that is already checked locally must not masquerade as unverified.
    report[check]["unverified_targets"] = [target]
    with pytest.raises(ValueError, match=f"invalid {check} unverified targets"):
        compare_reports(report, report)


@pytest.mark.parametrize("alias", [
    "https://EXAMPLE.test/de/index.html",
    "https://example.test:443/de/index.html",
    "https://example.test/de/%69ndex.html",
    "https://example.test/en/../de/index.html",
    "https://example.test/de/index.html#fragment",
])
def test_comparison_rejects_canonical_target_alias_falsely_marked_unverified(alias):
    report = _report(
        [("en/index.html", []), ("de/index.html", [])],
        checks=("canonical_consistency",),
    )
    report["canonical_consistency"]["unverified_targets"] = [{
        "path": "en/index.html", "target": alias,
    }]
    with pytest.raises(ValueError, match="invalid canonical_consistency unverified targets"):
        compare_reports(report, report)


def test_archived_hreflang_target_alias_retains_exact_url_matching():
    report = _report(
        [("en/index.html", []), ("de/index.html", [])],
        checks=("hreflang_reciprocity",),
    )
    report["hreflang_reciprocity"]["unverified_targets"] = [{
        "path": "en/index.html", "target": "https://EXAMPLE.test/de/index.html",
    }]
    assert compare_reports(report, report)["status"] == "pass"


@pytest.mark.parametrize("json_output", [True, False])
def test_cli_rejects_archived_canonical_alias_as_input_error(tmp_path, capsys, json_output):
    report = _report(
        [("en/index.html", []), ("de/index.html", [])],
        checks=("canonical_consistency",),
    )
    report["canonical_consistency"]["unverified_targets"] = [{
        "path": "en/index.html", "target": "https://EXAMPLE.test/de/index.html",
    }]
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps(report), encoding="utf-8")
    after.write_text(json.dumps(report), encoding="utf-8")

    args = [str(before), str(after)] + (["--json"] if json_output else [])
    assert main(args) == 2
    output = capsys.readouterr()
    if json_output:
        assert json.loads(output.out)["status"] == "error"
        assert "canonical_consistency" in json.loads(output.out)["error"]
    else:
        assert "canonical_consistency" in output.err
        assert not output.out


@pytest.mark.parametrize("json_output", [True, False])
def test_cli_rejects_falsely_unverified_manifest_target(tmp_path, capsys, json_output):
    report = _report(
        [("en/index.html", []), ("de/index.html", [])],
        checks=("canonical_consistency",),
    )
    report["canonical_consistency"]["unverified_targets"] = [{
        "path": "en/index.html",
        "target": "https://example.test/de/index.html",
    }]
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps(report), encoding="utf-8")
    after.write_text(json.dumps(report), encoding="utf-8")

    args = [str(before), str(after)] + (["--json"] if json_output else [])
    assert main(args) == 2
    output = capsys.readouterr()
    if json_output:
        assert json.loads(output.out)["status"] == "error"
        assert "canonical_consistency" in json.loads(output.out)["error"]
    else:
        assert "canonical_consistency" in output.err
        assert not output.out


@pytest.mark.parametrize("json_output", [True, False])
@pytest.mark.parametrize("missing", [True, False])
def test_cli_rejects_corrupt_cross_page_counts_as_input_error(
    tmp_path, capsys, json_output, missing,
):
    report = _report([("en/index.html", [])], checks=("canonical_consistency",))
    if missing:
        del report["canonical_consistency"]["internal_targets_checked"]
    else:
        report["canonical_consistency"]["internal_targets_checked"] = -1
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps(report), encoding="utf-8")
    after.write_text(json.dumps(report), encoding="utf-8")

    args = [str(before), str(after), "--fail-on-new-unverified-targets"]
    if json_output:
        args.append("--json")
    assert main(args) == 2
    output = capsys.readouterr()
    if json_output:
        assert json.loads(output.out)["status"] == "error"
        assert "canonical_consistency" in json.loads(output.out)["error"]
    else:
        assert "canonical_consistency" in output.err
        assert not output.out


@pytest.mark.parametrize("json_output", [True, False])
def test_cli_strict_unverified_gate_without_check_data_is_input_error(
    tmp_path, capsys, monkeypatch, json_output,
):
    report = _report([("en/index.html", [])])
    before, after = tmp_path / "baseline.json", tmp_path / "candidate.json"
    before.write_text(json.dumps(report), encoding="utf-8")
    after.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: (
        pytest.fail("Offline comparison must not fetch URLs")
    ))
    args = [str(before), str(after)] + (["--json"] if json_output else [])
    assert main(args) == 0
    capsys.readouterr()
    assert main(args + ["--fail-on-new-unverified-targets"]) == 2
    output = capsys.readouterr()
    if json_output:
        result = json.loads(output.out)
        assert result["status"] == "error"
        assert "cross-page check" in result["error"]
    else:
        assert "cross-page check" in output.err
        assert not output.out


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


@pytest.mark.parametrize("check,item", [
    ("canonical_consistency", {"path": "en/index.html", "target": "https://outside.test/"}),
    ("hreflang_reciprocity", {"path": "en/index.html", "target": "https://outside.test/de/"}),
    ("hreflang_clusters", {
        "path": "en/index.html", "language": "de", "target": "https://outside.test/de/",
    }),
])
def test_opt_in_unverified_targets_gate_blocks_only_new_targets(check, item):
    baseline = _report([("en/index.html", [])], checks=(check,))
    candidate = _report([("en/index.html", [])], checks=(check,))
    candidate[check]["unverified_targets"] = [item]

    # Existing informational behavior remains the default.
    assert compare_reports(baseline, candidate)["status"] == "pass"
    gated = compare_reports(baseline, candidate, fail_on_new_unverified_targets=True)
    assert gated["status"] == "regression"
    assert gated["summary"]["new_issues"] == 0
    assert gated["summary"]["new_unverified_targets"] == 1
    assert gated["new_unverified_targets"] == [{"check": check, **item}]
    assert compare_reports(candidate, candidate, fail_on_new_unverified_targets=True)["status"] == "pass"
    assert compare_reports(candidate, baseline, fail_on_new_unverified_targets=True)["status"] == "pass"


@pytest.mark.parametrize("before_count,after_count,expected_new", [
    (0, 2, 2), (1, 2, 1), (2, 2, 0), (2, 1, 0),
])
def test_strict_unverified_gate_tracks_repeated_target_occurrences(
    before_count, after_count, expected_new,
):
    target = {
        "path": "en/index.html", "language": "fr",
        "target": "https://outside.test/fr/",
    }
    baseline = _report([("en/index.html", [])], checks=("hreflang_clusters",))
    candidate = _report([("en/index.html", [])], checks=("hreflang_clusters",))
    baseline["hreflang_clusters"]["unverified_targets"] = [target] * before_count
    candidate["hreflang_clusters"]["unverified_targets"] = [target] * after_count

    diff = compare_reports(baseline, candidate, fail_on_new_unverified_targets=True)
    assert diff["summary"]["new_unverified_targets"] == expected_new
    assert diff["new_unverified_targets"] == [{"check": "hreflang_clusters", **target}] * expected_new
    assert diff["status"] == ("regression" if expected_new else "pass")


def test_actual_hreflang_cluster_duplicate_is_counted_as_new_unverified(tmp_path):
    artifact = tmp_path / "index.html"
    manifest = {"version": 1, "pages": [{
        "path": "index.html", "contract": {
            "url": "https://example.test/", "h1_max": 0,
        },
    }]}
    self_link = '<link rel="alternate" hreflang="en" href="https://example.test/">'
    external_link = '<link rel="alternate" hreflang="fr" href="https://outside.test/fr/">'
    artifact.write_text(f"<html><head>{self_link}{external_link}</head></html>", encoding="utf-8")
    baseline = verify_manifest(manifest, tmp_path, check_hreflang_clusters=True)
    artifact.write_text(
        f"<html><head>{self_link}{external_link}{external_link}</head></html>",
        encoding="utf-8",
    )
    candidate = verify_manifest(manifest, tmp_path, check_hreflang_clusters=True)

    assert len(baseline["hreflang_clusters"]["unverified_targets"]) == 1
    assert len(candidate["hreflang_clusters"]["unverified_targets"]) == 2
    diff = compare_reports(baseline, candidate, fail_on_new_unverified_targets=True)
    assert diff["summary"]["new_unverified_targets"] == 1
    assert diff["new_unverified_targets"][0]["target"] == "https://outside.test/fr/"


def test_cli_strict_gate_reports_extra_identical_unverified_target(tmp_path, capsys):
    target = {
        "path": "en/index.html", "language": "fr", "target": "https://outside.test/fr/",
    }
    baseline = _report([("en/index.html", [])], checks=("hreflang_clusters",))
    candidate = _report([("en/index.html", [])], checks=("hreflang_clusters",))
    baseline["hreflang_clusters"]["unverified_targets"] = [target]
    candidate["hreflang_clusters"]["unverified_targets"] = [target, target]
    before, after = tmp_path / "baseline.json", tmp_path / "candidate.json"
    before.write_text(json.dumps(baseline), encoding="utf-8")
    after.write_text(json.dumps(candidate), encoding="utf-8")

    assert main([str(before), str(after), "--json", "--fail-on-new-unverified-targets"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["summary"]["new_unverified_targets"] == 1
    assert result["new_unverified_targets"] == [{"check": "hreflang_clusters", **target}]


@pytest.mark.parametrize("json_output", [True, False])
def test_cli_opt_in_new_unverified_gate_exit_code_and_output(tmp_path, capsys, json_output):
    baseline = _report([("en/index.html", [])], checks=("canonical_consistency",))
    candidate = _report([("en/index.html", [])], checks=("canonical_consistency",))
    candidate["canonical_consistency"]["unverified_targets"] = [
        {"path": "en/index.html", "target": "https://elsewhere.test/"},
    ]
    before, after = tmp_path / "baseline.json", tmp_path / "candidate.json"
    before.write_text(json.dumps(baseline), encoding="utf-8")
    after.write_text(json.dumps(candidate), encoding="utf-8")
    args = [str(before), str(after)] + (["--json"] if json_output else [])

    assert main(args) == 0
    capsys.readouterr()
    assert main(args + ["--fail-on-new-unverified-targets"]) == 1
    output = capsys.readouterr().out
    if json_output:
        data = json.loads(output)
        assert data["status"] == "regression"
        assert data["summary"]["new_unverified_targets"] == 1
    else:
        assert "REGRESSION" in output
        assert "UNVERIFIED" in output
        assert "en/index.html" in output
        assert "https://elsewhere.test/" in output


def test_opt_in_unverified_gate_detects_actual_changed_canonical_in_built_html(tmp_path):
    artifact = tmp_path / "index.html"
    manifest = {"version": 1, "pages": [{
        "path": "index.html",
        "contract": {"url": "https://example.test/", "title_present": True},
    }]}
    artifact.write_text(
        '<html><head><title>OK</title><link rel="canonical" href="https://example.test/">'
        '</head><body></body></html>', encoding="utf-8",
    )
    baseline = verify_manifest(manifest, tmp_path, check_canonical_consistency=True)
    artifact.write_text(
        '<html><head><title>OK</title><link rel="canonical" href="https://outside.test/">'
        '</head><body></body></html>', encoding="utf-8",
    )
    candidate = verify_manifest(manifest, tmp_path, check_canonical_consistency=True)
    assert baseline["status"] == candidate["status"] == "pass"
    assert compare_reports(baseline, candidate)["status"] == "pass"
    result = compare_reports(baseline, candidate, fail_on_new_unverified_targets=True)
    assert result["status"] == "regression"
    assert result["summary"]["new_unverified_targets"] == 1
    assert result["new_unverified_targets"][0]["target"] == "https://outside.test/"


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


@pytest.mark.parametrize("has_regression", [False, True])
def test_archived_comparison_json_file_is_complete_utf8_and_offline(
    tmp_path, capsys, monkeypatch, has_regression,
):
    baseline = _report([("en/index.html", [_issue("déjà vu")])])
    candidate = _report([
        ("en/index.html", [_issue("déjà vu")]),
        ("fr/index.html", [_issue("nouveau")] if has_regression else []),
    ])
    before, after = tmp_path / "baseline.json", tmp_path / "candidate.json"
    before.write_text(json.dumps(baseline), encoding="utf-8")
    after.write_text(json.dumps(candidate), encoding="utf-8")
    artifact = tmp_path / "reports" / "comparison.json"
    artifact.parent.mkdir()
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: (
        pytest.fail("Archived comparison must remain offline")
    ))

    expected_exit = 1 if has_regression else 0
    assert main([str(before), str(after), "--json"]) == expected_exit
    expected = json.loads(capsys.readouterr().out)
    assert main([str(before), str(after), "--json-file", str(artifact)]) == expected_exit
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    assert artifact.read_bytes().startswith(b"{")
    assert json.loads(artifact.read_text(encoding="utf-8")) == expected
    assert expected["summary"]["existing_issues"] == 1
    assert expected["summary"]["new_issues"] == int(has_regression)


def test_archived_comparison_json_file_refuses_overwrite_and_missing_parent(tmp_path, capsys):
    before, after = tmp_path / "baseline.json", tmp_path / "candidate.json"
    report = json.dumps(_report([("en/index.html", [])]))
    before.write_text(report, encoding="utf-8")
    after.write_text(report, encoding="utf-8")
    existing = tmp_path / "existing.json"
    existing.write_bytes(b"DO NOT REPLACE")
    for destination in (existing, before, after, tmp_path / "absent" / "result.json"):
        assert main([str(before), str(after), "--json-file", str(destination)]) == 2
        assert capsys.readouterr().out == ""
    assert existing.read_bytes() == b"DO NOT REPLACE"
    assert before.read_text(encoding="utf-8") == report
    assert after.read_text(encoding="utf-8") == report
    assert not (tmp_path / "absent").exists()


def test_archived_comparison_json_file_invalid_input_does_not_create_artifact(
    tmp_path, capsys,
):
    before, after = tmp_path / "baseline.json", tmp_path / "candidate.json"
    before.write_text(json.dumps(_report([("en/index.html", [])])), encoding="utf-8")
    after.write_text("{", encoding="utf-8")
    artifact = tmp_path / "comparison.json"
    assert main([str(before), str(after), "--json-file", str(artifact)]) == 2
    captured = capsys.readouterr()
    assert "Invalid or incompatible report" in captured.err
    assert captured.out == ""
    assert not artifact.exists()


def test_archived_comparison_json_file_rejects_stdout_json_mode(tmp_path):
    with pytest.raises(SystemExit) as error:
        main([
            str(tmp_path / "baseline.json"),
            str(tmp_path / "candidate.json"),
            "--json", "--json-file", str(tmp_path / "comparison.json"),
        ])
    assert error.value.code == 2
    assert not (tmp_path / "comparison.json").exists()


@pytest.mark.parametrize("observed", ["Missing H1", "line one\nline two", "x" * 600])
def test_cli_new_issue_shows_bounded_actionable_expected_and_observed(
    tmp_path, capsys, observed,
):
    baseline = _report([("en/index.html", [])])
    candidate = _report([("en/index.html", [_issue(observed)])])
    before, after = tmp_path / "baseline.json", tmp_path / "candidate.json"
    before.write_text(json.dumps(baseline), encoding="utf-8")
    after.write_text(json.dumps(candidate), encoding="utf-8")

    assert main([str(before), str(after)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2  # An embedded newline cannot forge another CI finding.
    assert "NEW en/index.html" in lines[1]
    assert "title_presence" in lines[1]
    assert "expected True" in lines[1]
    assert "observed " in lines[1]
    assert len(lines[1]) < 400  # Unexpected page content must not flood CI logs.
    if observed == "Missing H1":
        assert "observed 'Missing H1'" in lines[1]
    elif "\n" in observed:
        assert "line one\\nline two" in lines[1]


def test_cli_human_output_escapes_untrusted_identifiers_without_forged_lines(tmp_path, capsys):
    bad_path = "index.html\nFORGED PATH"
    baseline = _report([(bad_path, [])], strict=True, checks=("hreflang_clusters",))
    candidate = _report(
        [(bad_path, [{"code": "title\nFORGED ISSUE", "expected": True, "actual": False}])],
        unlisted=("other\nFORGED UNLISTED.html",), strict=True,
        checks=("hreflang_clusters",),
    )
    candidate["hreflang_clusters"]["unverified_targets"] = [{
        "path": bad_path, "language": "de\nFORGED LOCALE",
        "target": "https://elsewhere.test/a\nFORGED TARGET",
    }]
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps(baseline), encoding="utf-8")
    after.write_text(json.dumps(candidate), encoding="utf-8")

    assert main([str(before), str(after), "--fail-on-new-unverified-targets"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 5  # Summary, issue, unlisted, unverified summary and target.
    assert all(not line.startswith("FORGED") for line in lines)
    assert "title\\nFORGED ISSUE" in lines[1]
    assert "index.html\\nFORGED PATH" in lines[1]
    assert "other\\nFORGED UNLISTED.html" in lines[2]
    assert "de\\nFORGED LOCALE" in lines[4]
    assert "a\\nFORGED TARGET" in lines[4]


def test_cli_human_output_bounds_long_identifiers_and_retains_json(tmp_path, capsys):
    long_path = "x" * 800 + ".html"
    long_target = "https://elsewhere.test/" + "q" * 1000
    baseline = _report([("index.html", [])], strict=True, checks=("canonical_consistency",))
    candidate = _report(
        [(long_path, [{"code": "code" + "k" * 800, "expected": True, "actual": False}])],
        unlisted=("u" * 1000 + ".html",), strict=True,
        checks=("canonical_consistency",),
    )
    candidate["canonical_consistency"]["unverified_targets"] = [
        {"path": long_path, "target": long_target},
    ]
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps(baseline), encoding="utf-8")
    after.write_text(json.dumps(candidate), encoding="utf-8")

    assert main([str(before), str(after), "--fail-on-new-unverified-targets"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 6  # Summary, new issue, removed, unlisted, count, target.
    assert max(map(len, lines)) < 550
    assert "..." in lines[1] and "..." in lines[-1]

    assert main([str(before), str(after), "--json", "--fail-on-new-unverified-targets"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["new_issues"][0]["path"] == long_path
    assert data["new_unverified_targets"][0]["target"] == long_target


def test_cli_human_output_limits_many_findings_without_losing_json(tmp_path, capsys, monkeypatch):
    baseline = _report(
        [("index.html", [])] + [(f"old-{i}.html", []) for i in range(25)],
        strict=True, checks=("hreflang_clusters",),
    )
    candidate = _report(
        [("index.html", [_issue(f"value-{i}") for i in range(25)])],
        unlisted=tuple(f"new-{i}.html" for i in range(25)),
        strict=True, checks=("hreflang_clusters",),
    )
    candidate["hreflang_clusters"]["unverified_targets"] = [
        {"path": "index.html", "language": f"lang-{i}",
         "target": f"https://outside.test/{i}"}
        for i in range(25)
    ]
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps(baseline), encoding="utf-8")
    after.write_text(json.dumps(candidate), encoding="utf-8")
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: (
        pytest.fail("Offline comparator must never fetch URLs")
    ))

    args = [str(before), str(after), "--fail-on-new-unverified-targets"]
    assert main(args) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("REGRESSION 25 new issues")
    assert sum(line.startswith("  NEW ") for line in lines) == 20
    assert sum(line.startswith("  REMOVED ") for line in lines) == 20
    assert sum(line.startswith("  UNLISTED ") for line in lines) == 20
    assert sum(line.startswith("    hreflang_clusters ") for line in lines) == 20
    assert len(lines) == 86  # Summary, four capped sections, and target count.
    assert sum("5 more" in line and "--json" in line for line in lines) == 4

    assert main([*args, "--json"]) == 1
    full = json.loads(capsys.readouterr().out)
    assert full["status"] == "regression"
    assert [len(full[key]) for key in (
        "new_issues", "removed_pages", "new_unlisted", "new_unverified_targets",
    )] == [25, 25, 25, 25]


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
