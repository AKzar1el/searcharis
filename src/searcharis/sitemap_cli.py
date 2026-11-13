"""Offline XML sitemap checks against explicit, reviewed SEO indexability intent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from searcharis.cli_error_display import human_input_error
from searcharis.site_ci import read_manifest
from searcharis.sitemap_ci import verify_sitemap_manifest


def _display(value: str) -> str:
    text = repr(value)
    return text if len(text) <= 140 else text[:137] + "..."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check local XML sitemap against reviewed indexability assertions",
    )
    parser.add_argument("manifest", type=Path, help="Reviewed SEO page manifest (version 1)")
    parser.add_argument(
        "sitemaps", nargs="+", type=Path,
        help="One or more explicit local sitemap XML URL sets (not indexes)",
    )
    parser.add_argument(
        "--require-indexability-coverage", action="store_true",
        help="Require explicit indexable true/false for every reviewed manifest page",
    )
    parser.add_argument(
        "--reject-noncanonical-urls", action="store_true",
        help="Reject sitemap URLs for reviewed indexable pages with a different canonical",
    )
    parser.add_argument("--json", action="store_true", help="Print structured JSON outcome")
    args = parser.parse_args(argv)

    try:
        report = verify_sitemap_manifest(
            read_manifest(args.manifest), args.sitemaps,
            require_indexability_coverage=args.require_indexability_coverage,
            reject_noncanonical_urls=args.reject_noncanonical_urls,
        )
    except (OSError, UnicodeError, ValueError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        summary = report["summary"]
        print(
            f"{report['status'].upper()} {summary['required']} required, "
            f"{summary['excluded']} excluded, "
            f"{summary['unasserted_pages']} without indexability assertion, "
            f"{summary['sitemap_urls']} sitemap URLs"
        )
        for url in report["missing_required"]:
            print(f"  Missing required: {_display(url)}")
        for url in report["present_excluded"]:
            print(f"  Unexpected noindex URL: {_display(url)}")
        for url in report.get("present_noncanonical", []):
            print(f"  Noncanonical reviewed URL in sitemap: {_display(url)}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
