"""Run offline SEO checks over an explicit manifest of built HTML files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from searcharis.site_ci import read_manifest, verify_manifest


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
        "--check-canonical-consistency",
        action="store_true",
        help="Check actual canonical links and self-canonical targets among manifest pages",
    )
    parser.add_argument("--json", action="store_true", help="Print structured JSON findings")
    args = parser.parse_args(argv)

    try:
        report = verify_manifest(
            read_manifest(args.manifest),
            args.build_dir,
            require_full_coverage=args.require_full_coverage,
            check_hreflang_reciprocity=args.check_hreflang_reciprocity,
            check_canonical_consistency=args.check_canonical_consistency,
        )
    except (OSError, UnicodeError, ValueError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid input: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
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
                print(f"  FAIL {page['path']} ({page['checked_url']})")
                for issue in page["issues"]:
                    print(
                        f"    {issue['code']}: expected {issue['expected']!r}, "
                        f"observed {issue['actual']!r}"
                    )
        if coverage["unlisted"]:
            print("  Unlisted HTML: " + ", ".join(coverage["unlisted"]))
        if args.check_hreflang_reciprocity:
            links = report["hreflang_reciprocity"]
            print(
                f"  Hreflang: {links['checked_pairs']} internal pairs checked, "
                f"{len(links['unverified_targets'])} targets outside manifest (not verified)"
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
