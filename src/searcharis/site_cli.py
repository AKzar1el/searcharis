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
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Print structured JSON findings")
    output.add_argument(
        "--json-file", type=Path, metavar="PATH",
        help="Write structured UTF-8 JSON findings to a new file without overwriting",
    )
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

    if args.json_file:
        try:
            if args.json_file.resolve().is_relative_to(args.build_dir.resolve()):
                raise ValueError("JSON report file must be outside the build directory")
            # Exclusive create preserves existing CI artifacts; writing UTF-8
            # avoids the default UTF-16LE stdout redirection of PowerShell 5.1.
            with args.json_file.open("x", encoding="utf-8", newline="\n") as destination:
                destination.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        except (OSError, UnicodeError, ValueError) as error:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
            return 2
    elif args.json:
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
        for page in report["pages"]:
            if page["status"] == "fail":
                print(f"  FAIL {_label(page['path'])} ({_label(page['checked_url'])})")
                for issue in page["issues"]:
                    print(
                        f"    {_label(issue['code'])}: expected {_preview(issue['expected'])}, "
                        f"observed {_preview(issue['actual'])}"
                    )
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
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
