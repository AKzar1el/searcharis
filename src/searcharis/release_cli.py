"""One opt-in local release gate for built HTML and reviewed XML sitemap intent.

No contract URL is fetched: only the supplied build files, manifest and local
XML URL sets are read. This does not replace either independent checker.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from searcharis.cli_error_display import human_input_error
from searcharis.site_ci import read_manifest, verify_manifest
from searcharis.site_junit import build_release_junit
from searcharis.sitemap_ci import verify_sitemap_manifest


def _preview(value: object, *, limit: int = 120) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check built HTML and explicit local sitemap URL sets in one offline CI gate",
    )
    parser.add_argument("manifest", type=Path, help="Reviewed SEO manifest (version 1)")
    parser.add_argument("build_dir", type=Path, help="Local compiled static HTML directory")
    parser.add_argument(
        "sitemaps", nargs="+", type=Path,
        help="Explicit local sitemap XML URL sets (no index resolution or URL fetching)",
    )
    parser.add_argument(
        "--require-full-coverage", action="store_true",
        help="Fail if built HTML pages are missing from the reviewed manifest",
    )
    parser.add_argument(
        "--require-indexability-coverage", action="store_true",
        help="Require explicit indexable true/false for every reviewed manifest page",
    )
    parser.add_argument(
        "--reject-noncanonical-urls", action="store_true",
        help="Reject sitemap URLs for reviewed indexable pages with a different canonical",
    )
    parser.add_argument(
        "--check-hreflang-reciprocity", action="store_true",
        help="Require reciprocal hreflang links between reviewed built pages",
    )
    parser.add_argument(
        "--check-hreflang-clusters", action="store_true",
        help="Check consistent hreflang locale mappings among reviewed built pages",
    )
    parser.add_argument(
        "--check-canonical-consistency", action="store_true",
        help="Check internal canonical targets and self-links among reviewed pages",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Print both reports as JSON")
    output.add_argument(
        "--json-file", type=Path, metavar="PATH",
        help="Write both reports to a new UTF-8 JSON file; never overwrite",
    )
    output.add_argument(
        "--junit", action="store_true",
        help="Print a JUnit XML suite of blocking HTML and sitemap findings",
    )
    output.add_argument(
        "--junit-file", type=Path, metavar="PATH",
        help="Write JUnit XML to a new file outside the build; never overwrite",
    )
    args = parser.parse_args(argv)

    try:
        reviewed_manifest = read_manifest(args.manifest)
        site = verify_manifest(
            reviewed_manifest,
            args.build_dir,
            require_full_coverage=args.require_full_coverage,
            check_hreflang_reciprocity=args.check_hreflang_reciprocity,
            check_hreflang_clusters=args.check_hreflang_clusters,
            check_canonical_consistency=args.check_canonical_consistency,
        )
        sitemap = verify_sitemap_manifest(
            reviewed_manifest, args.sitemaps,
            require_indexability_coverage=args.require_indexability_coverage,
            reject_noncanonical_urls=args.reject_noncanonical_urls,
        )
    except (OSError, UnicodeError, ValueError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
        return 2

    status = "pass" if site["status"] == sitemap["status"] == "pass" else "fail"
    if args.junit or args.junit_file:
        junit = build_release_junit(site, sitemap)
        if args.junit_file:
            try:
                target = args.junit_file.resolve()
                if target.is_relative_to(args.build_dir.resolve()):
                    raise ValueError("JUnit report must be outside the build directory")
                # Create exclusively, preserving immutable build inputs and old reports.
                with args.junit_file.open("x", encoding="utf-8", newline="\n") as report:
                    report.write(junit + "\n")
            except (OSError, UnicodeError, ValueError) as error:
                print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
                return 2
        else:
            print(junit)
    elif args.json or args.json_file:
        serialized = json.dumps(
            {"status": status, "site": site, "sitemap": sitemap},
            indent=2, sort_keys=True,
        )
        if args.json_file:
            try:
                target = args.json_file.resolve()
                if target.is_relative_to(args.build_dir.resolve()):
                    raise ValueError("JSON report must be outside the build directory")
                # Exclusive creation preserves earlier CI artifacts; direct UTF-8
                # avoids Windows PowerShell's UTF-16LE stdout redirection.
                with args.json_file.open("x", encoding="utf-8", newline="\n") as report:
                    report.write(serialized + "\n")
            except (OSError, UnicodeError, ValueError) as error:
                print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
                return 2
        else:
            print(serialized)
    else:
        pages = site["summary"]
        xml = sitemap["summary"]
        print(f"{site['status'].upper()} HTML: {pages['passed']}/{pages['checked']} "
              "reviewed pages passed")
        for page in site["pages"]:
            if page["status"] == "fail":
                codes = ", ".join(_preview(issue["code"]) for issue in page["issues"])
                print(f"  FAIL {_preview(page['path'])}: {codes}")
        unlisted = site["coverage"]["unlisted"]
        if unlisted:
            print(f"  Unlisted built HTML: {len(unlisted)} "
                  "(use --json for exact paths)")
        print(f"{sitemap['status'].upper()} sitemap: {xml['required']} required, "
              f"{xml['excluded']} excluded, {xml['sitemap_urls']} URLs examined")
        for url in sitemap["missing_required"]:
            print(f"  Missing required: {_preview(url)}")
        for url in sitemap["present_excluded"]:
            print(f"  Unexpected noindex: {_preview(url)}")
        for url in sitemap.get("present_noncanonical", []):
            print(f"  Noncanonical reviewed sitemap URL: {_preview(url)}")
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
