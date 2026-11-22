"""Compare two offline site reports, blocking newly introduced regressions only.

Both inputs are previously generated local site_cli --json artifacts.
No URLs are fetched and no deployment or provider integrations are invoked.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from searcharis.cli_error_display import human_input_error
from searcharis.comparison_markdown import build_comparison_markdown
from searcharis.contract_ci import _is_canonical_url, _is_http_url
from searcharis.site_ci import _manifest_path, _page_url_identity
from searcharis.site_junit import build_comparison_junit
from searcharis.site_sarif import build_comparison_sarif

_MAX_REPORT_BYTES = 16_000_000
_MAX_HUMAN_DETAILS = 20
_CHECKS = ("canonical_consistency", "hreflang_clusters", "hreflang_reciprocity")
_REVIEWED_CHECKS = (
    ("reviewed_navigation", "checked_links", ("unreviewed_targets",)),
    ("internal_nofollow", "checked_links", ("nofollow_targets",)),
    ("reviewed_fragments", "checked_fragments",
     ("missing_targets", "ambiguous_targets", "unverified_targets")),
)
_UNIQUE_CHECKS = ("unique_titles", "unique_descriptions")


def _int(value: object) -> bool:
    return type(value) is int and value >= 0


def _valid_build_path(value: object) -> bool:
    # Archived reports must obey the same relative HTML path rules as live manifests.
    try:
        _manifest_path(value)
    except ValueError:
        return False
    return True


def _validate(report: object, name: str) -> tuple[dict, tuple[object, ...]]:
    if not isinstance(report, dict):
        raise ValueError(f"{name}: report must be a JSON object")
    try:
        # Python's JSON decoder accepts NaN/Infinity and can overflow 1e999
        # to infinity. Such values must not turn invalid CI input into PASS.
        json.dumps(report, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError(f"{name}: report contains non-finite or invalid JSON values") from error
    pages = report.get("pages")
    summary = report.get("summary")
    coverage = report.get("coverage")
    if not isinstance(pages, list) or not isinstance(summary, dict) or not isinstance(coverage, dict):
        raise ValueError(f"{name}: missing pages, summary or coverage")
    if not 1 <= len(pages) <= 10_000:
        raise ValueError(f"{name}: page count outside supported limits")

    paths: set[str] = set()
    urls: set[str] = set()
    url_identities: set[tuple[str, str, int | None, str, str]] = set()
    identities_by_path: dict[str, tuple[str, str, int | None, str, str]] = {}
    fingerprints: set[bool] = set()
    failed = 0
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError(f"{name}: invalid page entry")
        path, url, issues = page.get("path"), page.get("checked_url"), page.get("issues")
        if (not _valid_build_path(path) or not isinstance(url, str)
                or not _is_http_url(url)):
            raise ValueError(f"{name}: invalid page path or URL")
        # Historical reports may predate the manifest's URL alias checks.
        # Treat equivalent HTTP(S) resource spellings as one page even when
        # their original checked_url strings differ.
        identity = _page_url_identity(url)
        if path in paths or identity in url_identities:
            raise ValueError(f"{name}: duplicate page path or URL")
        paths.add(path)
        urls.add(url)
        url_identities.add(identity)
        identities_by_path[path] = identity
        has_fingerprint = "contract_sha256" in page
        fingerprints.add(has_fingerprint)
        if has_fingerprint:
            digest = page["contract_sha256"]
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise ValueError(f"{name}: invalid contract fingerprint for {path}")
        if not isinstance(issues, list) or any(
            not isinstance(issue, dict)
            or not isinstance(issue.get("code"), str)
            or not issue["code"]
            or "expected" not in issue
            or "actual" not in issue
            for issue in issues
        ):
            raise ValueError(f"{name}: invalid page issues")
        status = "fail" if issues else "pass"
        if page.get("status") != status:
            raise ValueError(f"{name}: page status disagrees with its issues")
        failed += bool(issues)

    if len(fingerprints) != 1:
        raise ValueError(f"{name}: inconsistent per-page contract fingerprints")

    if any(not _int(summary.get(key)) for key in ("checked", "passed", "failed")):
        raise ValueError(f"{name}: invalid summary counts")
    if (
        summary["checked"] != len(pages)
        or summary["passed"] != len(pages) - failed
        or summary["failed"] != failed
    ):
        raise ValueError(f"{name}: summary counts disagree with page findings")

    unlisted = coverage.get("unlisted")
    strict = coverage.get("require_full_coverage")
    if (
        not isinstance(unlisted, list)
        or any(not _valid_build_path(path) for path in unlisted)
        or len(set(unlisted)) != len(unlisted)
        or not isinstance(strict, bool)
        or not _int(coverage.get("checked"))
        or not _int(coverage.get("discovered"))
        or coverage["checked"] != len(pages)
        or coverage["discovered"] < len(pages)
        or coverage["discovered"] != len(pages) + len(unlisted)
    ):
        raise ValueError(f"{name}: invalid coverage data")
    # The site checker counts case-distinct HTML artifacts separately on
    # case-sensitive builds; only exact duplicates invalidate its coverage.
    listed_paths = {page["path"] for page in pages}
    if any(path in listed_paths for path in unlisted):
        raise ValueError(f"{name}: listed page also appears as unlisted")
    expected_status = "fail" if failed or (strict and unlisted) else "pass"
    if report.get("status") != expected_status:
        raise ValueError(f"{name}: overall status disagrees with findings")

    for check in _CHECKS:
        if check not in report:
            continue
        data = report[check]
        count_key = (
            "internal_targets_checked" if check == "canonical_consistency" else "checked_pairs"
        )
        if (
            not isinstance(data, dict)
            or not _int(data.get(count_key))
            or not isinstance(data.get("unverified_targets"), list)
        ):
            raise ValueError(f"{name}: invalid {check} data")
        # Each page contributes at most one off-self canonical destination;
        # each hreflang check counts each directed internal peer once.
        n_pages = len(pages)
        impossible_count = (
            data[count_key] + len(data["unverified_targets"]) > n_pages
            if check == "canonical_consistency"
            else data[count_key] > n_pages * (n_pages - 1)
        )
        if impossible_count:
            raise ValueError(f"{name}: invalid {check} data: impossible check count")
        if any(
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or item["path"] not in listed_paths
            or not isinstance(item.get("target"), str)
            # The canonical checker records only syntactically valid,
            # fragment-free HTTP(S) targets as outside-manifest links.
            or (check == "canonical_consistency"
                and not _is_canonical_url(item["target"]))
            or item["target"] in urls
            or (check == "canonical_consistency"
                and _page_url_identity(item["target"]) in url_identities)
            or (check == "hreflang_clusters" and not isinstance(item.get("language"), str))
            for item in data["unverified_targets"]
        ):
            raise ValueError(f"{name}: invalid {check} unverified targets")

    # A saved report with one of these opt-in checks disabled cannot be
    # compared against one with it enabled, even if both found zero issues.
    for check, count_key, target_keys in _REVIEWED_CHECKS:
        if check not in report:
            continue
        data = report[check]
        if not isinstance(data, dict) or not _int(data.get(count_key)):
            raise ValueError(f"{name}: invalid {check} data")
        for key in target_keys:
            targets = data.get(key)
            if not isinstance(targets, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("path"), str)
                or item["path"] not in listed_paths
                or not isinstance(item.get("target"), str)
                or not item["target"]
                for item in targets
            ):
                raise ValueError(f"{name}: invalid {check} {key}")

    # A saved navigation/fragment summary must describe the same failures as
    # its page issues; otherwise a modified archive could silently pass CI.
    for check, key, issue_code, count_key in (
        ("reviewed_navigation", "unreviewed_targets", "navigation_target_unreviewed",
         "checked_links"),
        ("reviewed_fragments", "missing_targets", "navigation_fragment_missing",
         "checked_fragments"),
        ("reviewed_fragments", "ambiguous_targets", "navigation_fragment_ambiguous",
         "checked_fragments"),
    ):
        page_findings = [
            (page["path"], issue["actual"])
            for page in pages for issue in page["issues"]
            if issue["code"] == issue_code
        ]
        if check not in report:
            if page_findings:
                raise ValueError(f"{name}: {check} issues without enabled policy")
            continue
        if any(not isinstance(target, str) for _, target in page_findings):
            raise ValueError(f"{name}: inconsistent {check} findings")
        findings = [
            (item["path"], item["target"]) for item in report[check][key]
        ]
        if (
            sorted(findings) != sorted(page_findings)
            or len(findings) > report[check][count_key]
            or len(set(findings)) != len(findings)
            or any(not _is_http_url(target) for _, target in findings)
        ):
            raise ValueError(f"{name}: inconsistent {check} findings")
        if check == "reviewed_navigation":
            # Generated navigation failures are same-origin links to *unreviewed*
            # URLs. Both a reviewed alias and a foreign origin are impossible.
            for path, target in findings:
                target_identity = _page_url_identity(target)
                if (
                    target_identity in url_identities
                    or target_identity[:3] != identities_by_path[path][:3]
                ):
                    raise ValueError(f"{name}: inconsistent {check} findings")

    nofollow_issues = [
        (page["path"], issue["actual"])
        for page in pages for issue in page["issues"]
        if issue["code"] == "internal_link_nofollow"
    ]
    if any(not isinstance(target, str) for _, target in nofollow_issues):
        raise ValueError(f"{name}: invalid internal_nofollow issue target")
    if "internal_nofollow" in report:
        data = report["internal_nofollow"]
        entries = [(item["path"], item["target"]) for item in data["nofollow_targets"]]
        if (
            sorted(entries) != sorted(nofollow_issues)
            or len(entries) > data["checked_links"]
            or len(set(entries)) != len(entries)
            or any(
                not _is_http_url(target)
                or _page_url_identity(target) not in url_identities
                or _page_url_identity(target)[:3] != identities_by_path[path][:3]
                for path, target in entries
            )
        ):
            raise ValueError(f"{name}: inconsistent internal_nofollow findings")
    elif nofollow_issues:
        raise ValueError(f"{name}: internal_nofollow issues without enabled policy")

    for check, issue_code in (
        ("unique_titles", "title_duplicate"),
        ("unique_descriptions", "description_duplicate"),
    ):
        if check in report:
            if report[check] is not True:
                raise ValueError(f"{name}: invalid {check} provenance")
        elif any(
            issue["code"] == issue_code for page in pages for issue in page["issues"]
        ):
            raise ValueError(f"{name}: {check} issues without enabled policy")

    # Opt-in critical-page inbound policy is a selected set of paths, not
    # merely a boolean: changing the targets can silently weaken a CI gate.
    inbound_paths: tuple[str, ...] = ()
    actual_missing = {
        page["path"] for page in pages
        if any(issue["code"] == "reviewed_inbound_link_missing" for issue in page["issues"])
    }
    if "reviewed_inbound" in report:
        inbound = report["reviewed_inbound"]
        if not isinstance(inbound, dict):
            raise ValueError(f"{name}: invalid reviewed_inbound data")
        required = inbound.get("required_paths")
        linked = inbound.get("linked_paths")
        missing = inbound.get("missing_paths")
        lists = (required, linked, missing)
        if (
            any(not isinstance(items, list) for items in lists)
            or not required
            or any(
                not isinstance(path, str) or path not in listed_paths
                for items in lists for path in items
            )
            or any(len(set(items)) != len(items) for items in lists)
            or set(linked) & set(missing)
            or set(linked) | set(missing) != set(required)
        ):
            raise ValueError(f"{name}: invalid reviewed_inbound data")
        if set(missing) != actual_missing:
            raise ValueError(f"{name}: invalid reviewed_inbound missing-page findings")
        inbound_paths = tuple(sorted(required))
    elif actual_missing:
        raise ValueError(f"{name}: reviewed_inbound issues without enabled policy")

    options = (
        strict,
        *(check in report for check in _CHECKS),
        *(check in report for check, _, _ in _REVIEWED_CHECKS),
        *(check in report for check in _UNIQUE_CHECKS),
        inbound_paths,
    )
    return report, options


def _issues(report: dict) -> dict[tuple[str, tuple[str, str, int | None, str, str], str, int], dict]:
    results = {}
    for page in report["pages"]:
        occurrences: dict[
            tuple[str, tuple[str, str, int | None, str, str], str], int
        ] = {}
        for issue in page["issues"]:
            # Equivalent spellings of one HTTP(S) resource remain the same
            # reviewed page across separately saved baseline/candidate reports.
            signature = (
                page["path"], _page_url_identity(page["checked_url"]),
                json.dumps(issue, sort_keys=True),
            )
            # Multiple identical findings are distinct failures, not a set.
            ordinal = occurrences.get(signature, 0)
            occurrences[signature] = ordinal + 1
            key = (*signature, ordinal)
            results[key] = {
                "path": page["path"],
                "checked_url": page["checked_url"],
                "issue": issue,
            }
    return results


def _unverified(report: dict) -> dict[tuple[str, str, int], dict]:
    results = {}
    for check in _CHECKS:
        if check not in report:
            continue
        occurrences: dict[str, int] = {}
        for item in report[check]["unverified_targets"]:
            # Canonical URL identity already ignores equivalent HTTP(S)
            # spellings inside a report; use it across archived builds too.
            # Hreflang targets intentionally retain exact URL matching.
            identity = (
                {**item, "target": _page_url_identity(item["target"])}
                if check == "canonical_consistency" else item
            )
            signature = json.dumps(identity, sort_keys=True)
            ordinal = occurrences.get(signature, 0)
            occurrences[signature] = ordinal + 1
            # A second identical outside-manifest link is still a new
            # occurrence, just as repeated ordinary page findings are.
            key = (check, signature, ordinal)
            results[key] = {"check": check, **item}
    return results


def compare_reports(
    baseline: object, candidate: object, *,
    fail_on_new_unlisted: bool = False,
    fail_on_new_unverified_targets: bool = False,
) -> dict:
    """Compare like-for-like reports with opt-in new coverage/target gates."""
    before, before_options = _validate(baseline, "Baseline")
    after, after_options = _validate(candidate, "Candidate")
    if before_options != after_options:
        raise ValueError(
            "Reports use different check options; regenerate both with identical "
            "coverage, canonical, hreflang, reviewed navigation/fragment, uniqueness "
            "and --require-inbound-link paths"
        )
    if fail_on_new_unverified_targets and not any(before_options[1:1 + len(_CHECKS)]):
        raise ValueError(
            "--fail-on-new-unverified-targets requires at least one cross-page check; "
            "regenerate both reports with --check-canonical-consistency, "
            "--check-hreflang-clusters or --check-hreflang-reciprocity"
        )

    old_fingerprints = {p["path"]: p["contract_sha256"] for p in before["pages"]
                        if "contract_sha256" in p}
    new_fingerprints = {p["path"]: p["contract_sha256"] for p in after["pages"]
                        if "contract_sha256" in p}
    if bool(old_fingerprints) != bool(new_fingerprints):
        raise ValueError("Reports use different contract fingerprint formats; regenerate both")
    for path in sorted(old_fingerprints.keys() & new_fingerprints.keys()):
        if old_fingerprints[path] != new_fingerprints[path]:
            raise ValueError(
                f"Reports use different SEO contracts for {path}; "
                "regenerate the approved baseline with matching assertions"
            )

    old_issues, current_issues = _issues(before), _issues(after)
    new_keys = sorted(current_issues.keys() - old_issues.keys())
    fixed_keys = sorted(old_issues.keys() - current_issues.keys())
    old_pages = {
        (p["path"], _page_url_identity(p["checked_url"])) for p in before["pages"]
    }
    current_pages = {
        (p["path"], _page_url_identity(p["checked_url"])) for p in after["pages"]
    }
    old_urls = {p["path"]: p["checked_url"] for p in before["pages"]}
    removed = [
        {"path": path, "checked_url": old_urls[path]}
        for path, _identity in sorted(old_pages - current_pages)
    ]
    new_unlisted = sorted(set(after["coverage"]["unlisted"]) - set(before["coverage"]["unlisted"]))
    before_unverified, after_unverified = _unverified(before), _unverified(after)
    new_unverified = [
        after_unverified[key] for key in sorted(after_unverified.keys() - before_unverified.keys())
    ]
    regressions = bool(
        new_keys or removed or ((before_options[0] or fail_on_new_unlisted) and new_unlisted)
        or (fail_on_new_unverified_targets and new_unverified)
    )
    return {
        "status": "regression" if regressions else "pass",
        "summary": {
            "new_issues": len(new_keys),
            "existing_issues": len(current_issues.keys() & old_issues.keys()),
            "resolved_issues": len(fixed_keys),
            "removed_pages": len(removed),
            "new_unlisted": len(new_unlisted),
            "new_unverified_targets": len(new_unverified),
        },
        "new_issues": [current_issues[key] for key in new_keys],
        "resolved_issues": [old_issues[key] for key in fixed_keys],
        "removed_pages": removed,
        "new_unlisted": new_unlisted,
        "new_unverified_targets": new_unverified,
    }


def _read_report(path: Path) -> object:
    def unique_keys(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    with path.open("rb") as report_file:
        raw = report_file.read(_MAX_REPORT_BYTES + 1)
    if len(raw) > _MAX_REPORT_BYTES:
        raise ValueError(f"Report exceeds {_MAX_REPORT_BYTES} bytes: {path}")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)


def _preview(value: object, *, limit: int = 120) -> str:
    # repr escapes embedded newlines so a malformed observation cannot add
    # fake CI log lines, and a large HTML-derived value cannot flood the log.
    display = repr(value)
    return display if len(display) <= limit else display[:limit - 3] + "..."


def _label(value: str, *, limit: int = 120) -> str:
    # Report paths and codes are external JSON input, not trusted terminal text.
    # repr escapes control characters but preserves printable Unicode in logs.
    display = repr(value)[1:-1]
    return display if len(display) <= limit else display[:limit - 3] + "..."


def format_github_annotations(
    result: dict, *, gate_new_unlisted: bool = False,
    gate_new_unverified_targets: bool = False,
) -> list[str]:
    """Return bounded, new-only GitHub Actions error commands without fake source lines."""
    groups = [
        ("new issue", result["new_issues"]),
        ("removed page", result["removed_pages"]),
        ("new unlisted HTML", result["new_unlisted"] if gate_new_unlisted else []),
        (
            "new unverified target",
            result["new_unverified_targets"] if gate_new_unverified_targets else [],
        ),
    ]

    def snippet(value: object) -> str:
        text = repr(value)
        return text if len(text) <= 100 else text[:97] + "..."

    annotations: list[str] = []
    for index, (kind, items) in enumerate(groups):
        # Reserve one example for each later category, even if issues dominate.
        reserved = sum(bool(other) for _, other in groups[index + 1:])
        take = max(0, _MAX_HUMAN_DETAILS - len(annotations) - reserved)
        for item in items[:take]:
            if kind == "new issue":
                issue = item["issue"]
                message = (
                    f"{snippet(item['path'])} ({snippet(item['checked_url'])}): "
                    f"{snippet(issue['code'])}; expected {snippet(issue['expected'])}, "
                    f"observed {snippet(issue['actual'])}"
                )
            elif kind == "removed page":
                message = f"{snippet(item['path'])} ({snippet(item['checked_url'])})"
            elif kind == "new unlisted HTML":
                message = snippet(item)
            else:
                message = (
                    f"{snippet(item['check'])} {snippet(item['path'])}: "
                    f"{snippet(item['target'])}"
                )
            # A built path is not a repository source location. Do not invent
            # file/line metadata; escape workflow-command payload characters.
            message = (
                message.replace("%", "%25").replace("\r", "%0D")
                .replace("\n", "%0A").replace("::", ": :")
            )
            annotations.append(f"::error title=SEO {kind}::{message}")
    return annotations


def _print_omitted(items: list, category: str) -> None:
    remaining = len(items) - _MAX_HUMAN_DETAILS
    if remaining > 0:
        print(f"  ... {remaining} more {category}; use --json for complete findings")


def print_comparison(
    result: dict, *, as_json: bool = False, fail_on_new_unverified_targets: bool = False
) -> None:
    """Render one comparison identically for saved reports and live local builds."""
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    summary = result["summary"]
    print(
        f"{result['status'].upper()} "
        f"{summary['new_issues']} new issues, {summary['existing_issues']} existing, "
        f"{summary['resolved_issues']} resolved; {summary['removed_pages']} removed pages, "
        f"{summary['new_unlisted']} new unlisted HTML"
    )
    for item in result["new_issues"][:_MAX_HUMAN_DETAILS]:
        issue = item["issue"]
        print(
            f"  NEW {_label(item['path'])} ({_label(item['checked_url'])}): "
            f"{_label(issue['code'])} "
            f"(expected {_preview(issue['expected'])}, observed {_preview(issue['actual'])})"
        )
    _print_omitted(result["new_issues"], "new issues")
    for item in result["removed_pages"][:_MAX_HUMAN_DETAILS]:
        print(f"  REMOVED {_label(item['path'])} ({_label(item['checked_url'])})")
    _print_omitted(result["removed_pages"], "removed pages")
    for path in result["new_unlisted"][:_MAX_HUMAN_DETAILS]:
        print(f"  UNLISTED {_label(path)}")
    _print_omitted(result["new_unlisted"], "unlisted HTML paths")
    if summary["new_unverified_targets"]:
        label = "UNVERIFIED" if fail_on_new_unverified_targets else "NOTE"
        print(f"  {label} {summary['new_unverified_targets']} new targets not verified offline")
        if fail_on_new_unverified_targets:
            for item in result["new_unverified_targets"][:_MAX_HUMAN_DETAILS]:
                locale = f" [{_label(item['language'])}]" if "language" in item else ""
                print(
                    f"    {item['check']} {_label(item['path'])}{locale} "
                    f"-> {_label(item['target'])}"
                )
            _print_omitted(result["new_unverified_targets"], "unverified targets")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two local SEO site JSON reports")
    parser.add_argument("baseline", type=Path, help="Trusted baseline from site_cli --json")
    parser.add_argument("candidate", type=Path, help="Current build report from site_cli --json")
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable comparison",
    )
    parser.add_argument(
        "--json-file", type=Path, metavar="PATH",
        help="Write complete comparison JSON to a new UTF-8 file; never overwrite",
    )
    parser.add_argument(
        "--markdown-file", type=Path, metavar="PATH",
        help="Write a bounded Markdown regression review to a new UTF-8 file; never overwrite",
    )
    parser.add_argument(
        "--junit-file", type=Path, metavar="PATH",
        help="Write regression-only JUnit XML to a new UTF-8 file; never overwrite",
    )
    parser.add_argument(
        "--sarif-file", type=Path, metavar="PATH",
        help="Write regression-only SARIF 2.1.0 to a new UTF-8 file; never overwrite",
    )
    parser.add_argument(
        "--fail-on-new-unlisted", action="store_true",
        help="Block newly unlisted HTML artifacts without failing on existing coverage gaps",
    )
    parser.add_argument(
        "--fail-on-new-unverified-targets", action="store_true",
        help="Treat newly unverified canonical/hreflang targets as CI regressions",
    )
    args = parser.parse_args(argv)
    if args.json and (
        args.json_file or args.markdown_file or args.junit_file or args.sarif_file
    ):
        parser.error("--json stdout mode cannot be combined with file reports")
    try:
        baseline_report = _read_report(args.baseline)
        candidate_report = _read_report(args.candidate)
        result = compare_reports(
            baseline_report, candidate_report,
            fail_on_new_unlisted=args.fail_on_new_unlisted,
            fail_on_new_unverified_targets=args.fail_on_new_unverified_targets,
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid or incompatible report: {human_input_error(error)}", file=sys.stderr)
        return 2

    if args.json_file or args.markdown_file or args.junit_file or args.sarif_file:
        created_outputs: list[tuple[Path, tuple[int, int]]] = []
        try:
            outputs: list[tuple[Path, str]] = []
            gate_unlisted = (
                args.fail_on_new_unlisted or baseline_report["coverage"]["require_full_coverage"]
            )
            if args.json_file:
                outputs.append((args.json_file, json.dumps(result, indent=2, sort_keys=True)))
            if args.markdown_file:
                outputs.append((
                    args.markdown_file,
                    build_comparison_markdown(
                        result,
                        gate_new_unlisted=gate_unlisted,
                        gate_new_unverified_targets=args.fail_on_new_unverified_targets,
                    ),
                ))
            if args.junit_file:
                outputs.append((
                    args.junit_file,
                    build_comparison_junit(
                        result,
                        gate_new_unlisted=gate_unlisted,
                        gate_new_unverified_targets=args.fail_on_new_unverified_targets,
                    ),
                ))
            if args.sarif_file:
                outputs.append((
                    args.sarif_file,
                    json.dumps(build_comparison_sarif(
                        result, candidate_report,
                        gate_new_unlisted=gate_unlisted,
                        gate_new_unverified_targets=args.fail_on_new_unverified_targets,
                    ), indent=2, sort_keys=True),
                ))
            resolved = [path.resolve() for path, _ in outputs]
            if len(resolved) != len(set(resolved)):
                raise ValueError("All comparison report destinations must be distinct files")
            for (path, _), target in zip(outputs, resolved, strict=True):
                if target in {args.baseline.resolve(), args.candidate.resolve()}:
                    raise ValueError("Comparison report cannot replace an input report")
                if path.exists() or path.is_symlink():
                    raise FileExistsError(f"Comparison report already exists: {path}")
                if not path.parent.is_dir():
                    raise ValueError(f"Report parent must be an existing directory: {path.parent}")
            for path, payload in outputs:
                with path.open("x", encoding="utf-8", newline="\n") as output:
                    created = os.fstat(output.fileno())
                    created_outputs.append((path, (created.st_dev, created.st_ino)))
                    output.write(payload + "\n")
        except (OSError, UnicodeError, ValueError) as error:
            cleanup_errors: list[str] = []
            cleanup_skipped = 0
            for path, identity in reversed(created_outputs):
                try:
                    current = path.lstat()
                    # A second writer may have replaced a created report.
                    # Avoid deleting an unknown identity during best-effort rollback.
                    if (
                        not identity[1] or not current.st_ino
                        or (current.st_dev, current.st_ino) != identity
                    ):
                        cleanup_skipped += 1
                        continue
                    path.unlink()
                except OSError as cleanup_error:
                    cleanup_errors.append(
                        human_input_error(OSError(f"{path}: {cleanup_error}"))
                    )
            detail = f"Invalid report destination: {human_input_error(error)}"
            if cleanup_skipped:
                detail += (
                    f"; partial report cleanup skipped ({cleanup_skipped}): "
                    "report identity changed or unavailable"
                )
            if cleanup_errors:
                detail += "; partial report cleanup failed: " + "; ".join(cleanup_errors)
            print(detail, file=sys.stderr)
            return 2
    else:
        print_comparison(
            result,
            as_json=args.json,
            fail_on_new_unverified_targets=args.fail_on_new_unverified_targets,
        )
    return 1 if result["status"] == "regression" else 0


if __name__ == "__main__":
    raise SystemExit(main())
