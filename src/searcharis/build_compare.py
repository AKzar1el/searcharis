"""Compare two local static HTML builds directly, without report files or URLs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from searcharis.cli_error_display import human_input_error
from searcharis.report_compare import compare_reports, print_comparison
from searcharis.site_ci import read_manifest, verify_manifest
from searcharis.site_junit import build_comparison_junit
from searcharis.site_sarif import build_comparison_sarif
from searcharis.site_source_map import read_source_map


def _reject_shared_html_files(
    baseline_root: Path, candidate_root: Path, baseline: dict, candidate: dict
) -> None:
    """A hard link makes two otherwise disjoint builds one mutable snapshot."""
    baseline_files: dict[tuple[int, int], str] = {}
    for page in baseline["pages"]:
        info = (baseline_root / page["path"]).stat()
        if info.st_ino:  # Some filesystems do not expose a usable file ID.
            baseline_files[(info.st_dev, info.st_ino)] = page["path"]
    for page in candidate["pages"]:
        info = (candidate_root / page["path"]).stat()
        if info.st_ino and (info.st_dev, info.st_ino) in baseline_files:
            raise ValueError(
                "Baseline and candidate share a hard-linked HTML artifact: "
                f"{baseline_files[(info.st_dev, info.st_ino)]} and {page['path']}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gate new SEO regressions between two local static HTML builds"
    )
    parser.add_argument("manifest", type=Path, help="Reviewed SEO page manifest (version 1)")
    parser.add_argument("baseline_dir", type=Path, help="Trusted baseline static build directory")
    parser.add_argument("candidate_dir", type=Path, help="Candidate static build directory")
    parser.add_argument(
        "--require-full-coverage", action="store_true",
        help="Block newly unlisted HTML under strict full-coverage comparison",
    )
    parser.add_argument(
        "--check-hreflang-reciprocity", action="store_true",
        help="Check local return links between declared pages in each build",
    )
    parser.add_argument(
        "--check-hreflang-clusters", action="store_true",
        help="Check declared locale-to-URL maps in each build",
    )
    parser.add_argument(
        "--check-canonical-consistency", action="store_true",
        help="Check declared pages' actual canonical links in each build",
    )
    parser.add_argument(
        "--fail-on-new-unlisted", action="store_true",
        help="Block only newly unlisted HTML without blocking existing gaps",
    )
    parser.add_argument(
        "--fail-on-new-unverified-targets", action="store_true",
        help="Block newly unverified targets from enabled cross-page checks",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Print machine-readable comparison")
    output.add_argument(
        "--json-file", type=Path, metavar="PATH",
        help="Write complete comparison JSON to a new UTF-8 file; never overwrite",
    )
    output.add_argument(
        "--sarif", action="store_true",
        help="Print SARIF 2.1.0 findings only for new blocking regressions",
    )
    output.add_argument(
        "--sarif-file", type=Path, metavar="PATH",
        help="Write SARIF 2.1.0 to a new UTF-8 JSON file; never overwrite existing files",
    )
    output.add_argument(
        "--junit", action="store_true",
        help="Print JUnit XML test results only for new blocking regressions",
    )
    output.add_argument(
        "--junit-file", type=Path, metavar="PATH",
        help="Write JUnit XML directly to a new file; never overwrite existing files",
    )
    parser.add_argument(
        "--sarif-source-map", type=Path,
        help="Optional reviewed build-to-repository source mapping (SARIF only)",
    )
    args = parser.parse_args(argv)
    if args.sarif_source_map and not (args.sarif or args.sarif_file):
        parser.error("--sarif-source-map requires --sarif or --sarif-file")

    try:
        # Comparing a build with itself silently passes even when a CI input was
        # wired incorrectly. Resolve aliases before touching either build.
        baseline_root = args.baseline_dir.resolve()
        candidate_root = args.candidate_dir.resolve()
        if baseline_root == candidate_root:
            raise ValueError("Baseline and candidate must be different build directories")
        # Site verification walks HTML recursively. Nested roots would make one
        # snapshot include the other's pages and corrupt comparison coverage.
        if baseline_root.is_relative_to(candidate_root) or candidate_root.is_relative_to(
            baseline_root
        ):
            raise ValueError("Baseline and candidate must use non-overlapping build directories")
        manifest = read_manifest(args.manifest)
        checks = {
            "require_full_coverage": args.require_full_coverage,
            "check_hreflang_reciprocity": args.check_hreflang_reciprocity,
            "check_hreflang_clusters": args.check_hreflang_clusters,
            "check_canonical_consistency": args.check_canonical_consistency,
        }
        baseline = verify_manifest(manifest, args.baseline_dir, **checks)
        candidate = verify_manifest(manifest, args.candidate_dir, **checks)
        _reject_shared_html_files(baseline_root, candidate_root, baseline, candidate)
        sources = (
            read_source_map(args.sarif_source_map, candidate, source_root=Path.cwd())
            if args.sarif_source_map else {}
        )
        result = compare_reports(
            baseline, candidate,
            fail_on_new_unlisted=args.fail_on_new_unlisted,
            fail_on_new_unverified_targets=args.fail_on_new_unverified_targets,
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
        return 2

    if args.sarif or args.sarif_file:
        sarif = build_comparison_sarif(
            result, candidate, sources=sources,
            gate_new_unlisted=args.require_full_coverage or args.fail_on_new_unlisted,
            gate_new_unverified_targets=args.fail_on_new_unverified_targets,
        )
        serialized = json.dumps(sarif, indent=2, sort_keys=True)
        if args.sarif_file:
            try:
                target = args.sarif_file.resolve()
                if target.is_relative_to(baseline_root) or target.is_relative_to(
                    candidate_root
                ):
                    raise ValueError("SARIF file must be outside both build directories")
                # Exclusive creation protects existing artifacts; explicit UTF-8
                # avoids PowerShell 5.1 stdout redirection's UTF-16LE default.
                with args.sarif_file.open("x", encoding="utf-8", newline="\n") as report:
                    report.write(serialized + "\n")
            except (OSError, UnicodeError, ValueError) as error:
                print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
                return 2
        else:
            print(serialized)
    elif args.junit or args.junit_file:
        junit = build_comparison_junit(
            result,
            gate_new_unlisted=args.require_full_coverage or args.fail_on_new_unlisted,
            gate_new_unverified_targets=args.fail_on_new_unverified_targets,
        )
        if args.junit_file:
            try:
                target = args.junit_file.resolve()
                if target.is_relative_to(baseline_root) or target.is_relative_to(
                    candidate_root
                ):
                    raise ValueError("JUnit file must be outside both build directories")
                # Exclusive creation protects existing reports. Writing directly
                # avoids Windows PowerShell's UTF-16LE stdout redirection.
                with args.junit_file.open("x", encoding="utf-8", newline="\n") as report:
                    report.write(junit + "\n")
            except (OSError, UnicodeError, ValueError) as error:
                print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
                return 2
        else:
            print(junit)
    elif args.json_file:
        try:
            target = args.json_file.resolve()
            if target.is_relative_to(baseline_root) or target.is_relative_to(
                candidate_root
            ):
                raise ValueError("JSON file must be outside both build directories")
            # Write without shell redirection so Windows consumers receive UTF-8.
            with args.json_file.open("x", encoding="utf-8", newline="\n") as report:
                report.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        except (OSError, UnicodeError, ValueError) as error:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
            return 2
    else:
        print_comparison(
            result, as_json=args.json,
            fail_on_new_unverified_targets=args.fail_on_new_unverified_targets,
        )
    return 1 if result["status"] == "regression" else 0


if __name__ == "__main__":
    raise SystemExit(main())
