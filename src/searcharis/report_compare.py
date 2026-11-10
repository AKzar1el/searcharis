"""Compare two offline site reports, blocking newly introduced regressions only.

Both inputs are previously generated local site_cli --json artifacts.
No URLs are fetched and no deployment or provider integrations are invoked.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from searcharis.cli_error_display import human_input_error

_MAX_REPORT_BYTES = 16_000_000
_MAX_HUMAN_DETAILS = 20
_CHECKS = ("canonical_consistency", "hreflang_clusters", "hreflang_reciprocity")


def _int(value: object) -> bool:
    return type(value) is int and value >= 0


def _validate(report: object, name: str) -> tuple[dict, tuple[bool, ...]]:
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
    fingerprints: set[bool] = set()
    failed = 0
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError(f"{name}: invalid page entry")
        path, url, issues = page.get("path"), page.get("checked_url"), page.get("issues")
        if not isinstance(path, str) or not path or not isinstance(url, str) or not url:
            raise ValueError(f"{name}: invalid page path or URL")
        if path in paths or url in urls:
            raise ValueError(f"{name}: duplicate page path or URL")
        paths.add(path)
        urls.add(url)
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
        or any(not isinstance(path, str) or not path for path in unlisted)
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
        if not isinstance(data, dict) or not isinstance(data.get("unverified_targets"), list):
            raise ValueError(f"{name}: invalid {check} data")
        if any(
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("target"), str)
            or (check == "hreflang_clusters" and not isinstance(item.get("language"), str))
            for item in data["unverified_targets"]
        ):
            raise ValueError(f"{name}: invalid {check} unverified targets")

    options = (strict, *(check in report for check in _CHECKS))
    return report, options


def _issues(report: dict) -> dict[tuple[str, str, str, int], dict]:
    results = {}
    for page in report["pages"]:
        occurrences: dict[tuple[str, str, str], int] = {}
        for issue in page["issues"]:
            signature = (page["path"], page["checked_url"], json.dumps(issue, sort_keys=True))
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
            signature = json.dumps(item, sort_keys=True)
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
            "coverage, canonical and hreflang flags"
        )
    if fail_on_new_unverified_targets and not any(before_options[1:]):
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
    old_pages = {(p["path"], p["checked_url"]) for p in before["pages"]}
    current_pages = {(p["path"], p["checked_url"]) for p in after["pages"]}
    removed = [
        {"path": path, "checked_url": url}
        for path, url in sorted(old_pages - current_pages)
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


def _print_omitted(items: list, category: str) -> None:
    remaining = len(items) - _MAX_HUMAN_DETAILS
    if remaining > 0:
        print(f"  ... {remaining} more {category}; use --json for complete findings")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two local SEO site JSON reports")
    parser.add_argument("baseline", type=Path, help="Trusted baseline from site_cli --json")
    parser.add_argument("candidate", type=Path, help="Current build report from site_cli --json")
    parser.add_argument("--json", action="store_true", help="Print machine-readable comparison")
    parser.add_argument(
        "--fail-on-new-unlisted", action="store_true",
        help="Block newly unlisted HTML artifacts without failing on existing coverage gaps",
    )
    parser.add_argument(
        "--fail-on-new-unverified-targets", action="store_true",
        help="Treat newly unverified canonical/hreflang targets as CI regressions",
    )
    args = parser.parse_args(argv)
    try:
        result = compare_reports(
            _read_report(args.baseline), _read_report(args.candidate),
            fail_on_new_unlisted=args.fail_on_new_unlisted,
            fail_on_new_unverified_targets=args.fail_on_new_unverified_targets,
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid or incompatible report: {human_input_error(error)}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
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
            label = "UNVERIFIED" if args.fail_on_new_unverified_targets else "NOTE"
            print(f"  {label} {summary['new_unverified_targets']} new targets not verified offline")
            if args.fail_on_new_unverified_targets:
                for item in result["new_unverified_targets"][:_MAX_HUMAN_DETAILS]:
                    locale = f" [{_label(item['language'])}]" if "language" in item else ""
                    print(
                        f"    {item['check']} {_label(item['path'])}{locale} "
                        f"-> {_label(item['target'])}"
                    )
                _print_omitted(result["new_unverified_targets"], "unverified targets")
    return 1 if result["status"] == "regression" else 0


if __name__ == "__main__":
    raise SystemExit(main())
