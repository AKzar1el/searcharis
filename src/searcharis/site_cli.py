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
    parser.add_argument("--json", action="store_true", help="Print structured JSON findings")
    args = parser.parse_args(argv)

    try:
        report = verify_manifest(
            read_manifest(args.manifest),
            args.build_dir,
            require_full_coverage=args.require_full_coverage,
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
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
