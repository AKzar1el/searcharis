"""Run offline SEO checks over an explicit manifest of built HTML files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from searcharis.cli_error_display import human_input_error
from searcharis.site_ci import read_manifest, verify_manifest
from searcharis.site_sarif import build_sarif_report
from searcharis.site_source_map import read_source_map

_MAX_UNLISTED_DISPLAY = 20
_MAX_FAILED_PAGE_DISPLAY = 20
_MAX_ISSUES_PER_PAGE = 8


def _label(value: str, *, limit: int = 120) -> str:
    # Built artifact names and issue codes are untrusted CI log content.
    display = repr(value)[1:-1]
    return display if len(display) <= limit else display[:limit - 3] + "..."


def _preview(value: object, *, limit: int = 120) -> str:
    display = repr(value)
    return display if len(display) <= limit else display[:limit - 3] + "..."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify local built HTML against a page manifest")
    parser.add_argument("manifest", type=Path, help="JSON manifest (version 1)")
    parser.add_argument("build_dir", type=Path, help="Local static build output directory")
    parser.add_argument(
        "--require-full-coverage",
        action="store_true",
        help="Fail if the build contains HTML pages absent from the manifest",
    )
    parser.add_argument(
        "--check-hreflang-reciprocity",
        action="store_true",
        help="Require return links between built pages listed in the manifest",
    )
    parser.add_argument(
        "--check-hreflang-clusters",
        action="store_true",
        help="Require consistent local locale-to-URL maps across linked built pages",
    )
    parser.add_argument(
        "--check-canonical-consistency",
        action="store_true",
        help="Check actual canonical links and self-canonical targets among manifest pages",
    )
    parser.add_argument(
        "--check-reviewed-navigation", action="store_true",
        help="Fail if same-origin HTML navigation targets are absent from the reviewed manifest",
    )
    parser.add_argument(
        "--check-reviewed-fragments", action="store_true",
        help="Fail if links to reviewed same-origin pages use missing HTML fragment IDs",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Print structured JSON findings")
    output.add_argument(
        "--sarif",
        action="store_true",
        help="Print SARIF 2.1.0 issues for built HTML (or explicitly mapped sources)",
    )
    parser.add_argument(
        "--sarif-source-map",
        type=Path,
        help="Optional JSON map of built HTML to existing repository source files (SARIF only)",
    )
    args = parser.parse_args(argv)
    if args.sarif_source_map and not args.sarif:
        parser.error("--sarif-source-map requires --sarif")

    try:
        report = verify_manifest(
            read_manifest(args.manifest),
            args.build_dir,
            require_full_coverage=args.require_full_coverage,
            check_hreflang_reciprocity=args.check_hreflang_reciprocity,
            check_hreflang_clusters=args.check_hreflang_clusters,
            check_canonical_consistency=args.check_canonical_consistency,
            check_reviewed_navigation=args.check_reviewed_navigation,
            check_reviewed_fragments=args.check_reviewed_fragments,
        )
        sources = (
            read_source_map(args.sarif_source_map, report, source_root=Path.cwd())
            if args.sarif_source_map else {}
        )
    except (OSError, UnicodeError, ValueError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.sarif:
        print(json.dumps(build_sarif_report(report, sources=sources), indent=2, sort_keys=True))
    else:
        summary = report["summary"]
        coverage = report["coverage"]
        print(
            f"{report['status'].upper()} "
            f"{summary['passed']}/{summary['checked']} checked pages passed; "
            f"{coverage['discovered']} built HTML, {len(coverage['unlisted'])} unlisted"
        )
        failures = [page for page in report["pages"] if page["status"] == "fail"]
        for page in failures[:_MAX_FAILED_PAGE_DISPLAY]:
            print(f"  FAIL {_label(page['path'])} ({_label(page['checked_url'])})")
            for issue in page["issues"][:_MAX_ISSUES_PER_PAGE]:
                print(
                    f"    {_label(issue['code'])}: expected {_preview(issue['expected'])}, "
                    f"observed {_preview(issue['actual'])}"
                )
            remaining_issues = len(page["issues"]) - _MAX_ISSUES_PER_PAGE
            if remaining_issues > 0:
                print(f"    ... and {remaining_issues} more issues (use --json for all)")
        remaining_pages = len(failures) - _MAX_FAILED_PAGE_DISPLAY
        if remaining_pages > 0:
            print(f"  ... and {remaining_pages} more failed pages (use --json for all)")
        if coverage["unlisted"]:
            unlisted = coverage["unlisted"]
            visible = ", ".join(_label(path) for path in unlisted[:_MAX_UNLISTED_DISPLAY])
            remaining = len(unlisted) - _MAX_UNLISTED_DISPLAY
            if remaining > 0:
                visible += f", ... and {remaining} more (use --json for full list)"
            print("  Unlisted HTML: " + visible)
        if args.check_hreflang_reciprocity:
            links = report["hreflang_reciprocity"]
            print(
                f"  Hreflang: {links['checked_pairs']} internal pairs checked, "
                f"{len(links['unverified_targets'])} targets outside manifest (not verified)"
            )
        if args.check_hreflang_clusters:
            clusters = report["hreflang_clusters"]
            print(
                f"  Hreflang clusters: {clusters['checked_pairs']} directed peer pairs "
                f"checked, {len(clusters['unverified_targets'])} outside-manifest targets "
                "(not verified)"
            )
        if args.check_canonical_consistency:
            canonical = report["canonical_consistency"]
            print(
                f"  Canonicals: {canonical['internal_targets_checked']} internal targets checked, "
                f"{len(canonical['unverified_targets'])} targets outside manifest (not verified)"
            )
        if args.check_reviewed_navigation:
            navigation = report["reviewed_navigation"]
            print(
                f"  Navigation: {navigation['checked_links']} distinct local links checked, "
                f"{len(navigation['unreviewed_targets'])} unreviewed targets"
            )
        if args.check_reviewed_fragments:
            fragments = report["reviewed_fragments"]
            print(
                f"  Fragments: {fragments['checked_fragments']} reviewed targets checked, "
                f"{len(fragments['missing_targets'])} missing IDs, "
                f"{len(fragments['unverified_targets'])} outside-manifest targets"
            )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
