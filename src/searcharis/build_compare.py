"""Compare two local static HTML builds directly, without report files or URLs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from searcharis.cli_error_display import human_input_error
from searcharis.report_compare import compare_reports, print_comparison
from searcharis.site_ci import read_manifest, verify_manifest


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
    parser.add_argument("--json", action="store_true", help="Print machine-readable comparison")
    args = parser.parse_args(argv)

    try:
        # Comparing a build with itself silently passes even when a CI input was
        # wired incorrectly. Resolve aliases before touching either build.
        if args.baseline_dir.resolve() == args.candidate_dir.resolve():
            raise ValueError("Baseline and candidate must be different build directories")
        manifest = read_manifest(args.manifest)
        checks = {
            "require_full_coverage": args.require_full_coverage,
            "check_hreflang_reciprocity": args.check_hreflang_reciprocity,
            "check_hreflang_clusters": args.check_hreflang_clusters,
            "check_canonical_consistency": args.check_canonical_consistency,
        }
        baseline = verify_manifest(manifest, args.baseline_dir, **checks)
        candidate = verify_manifest(manifest, args.candidate_dir, **checks)
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

    print_comparison(
        result, as_json=args.json,
        fail_on_new_unverified_targets=args.fail_on_new_unverified_targets,
    )
    return 1 if result["status"] == "regression" else 0


if __name__ == "__main__":
    raise SystemExit(main())
