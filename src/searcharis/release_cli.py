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
from searcharis.release_markdown import build_release_markdown
from searcharis.release_sarif import build_release_sarif
from searcharis.site_ci import read_manifest, verify_manifest
from searcharis.site_junit import build_release_junit
from searcharis.site_source_map import read_source_map
from searcharis.sitemap_ci import verify_sitemap_manifest

_MAX_HUMAN_FINDINGS = 20
_MAX_CODES_PER_PAGE = 8


def _preview(value: object, *, limit: int = 120) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _bounded_human_lines(groups: tuple[list[str], ...]) -> tuple[list[str], int]:
    """Keep one example of each nonempty category inside the CI log budget."""
    selected: list[str] = []
    for index, group in enumerate(groups):
        reserved = sum(bool(later) for later in groups[index + 1:])
        available = max(0, _MAX_HUMAN_FINDINGS - len(selected) - reserved)
        selected.extend(group[:available])
    return selected, sum(map(len, groups)) - len(selected)


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
        "--reject-unreviewed-urls", action="store_true",
        help="Reject local sitemap URLs absent from all reviewed manifest pages/canonicals",
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
    parser.add_argument(
        "--check-reviewed-navigation", action="store_true",
        help="Fail if same-origin HTML navigation links target URLs absent from the manifest",
    )
    parser.add_argument(
        "--check-reviewed-fragments", action="store_true",
        help="Fail if reviewed same-origin HTML navigation fragments have no target ID",
    )
    parser.add_argument(
        "--check-unique-titles", action="store_true",
        help="Fail on duplicate normalized HTML titles among reviewed indexable pages on the same origin",
    )
    parser.add_argument(
        "--check-unique-descriptions", action="store_true",
        help="Fail on duplicate normalized meta descriptions among reviewed indexable same-origin pages",
    )
    parser.add_argument(
        "--require-inbound-link", action="append", default=[], metavar="PAGE_PATH",
        help="Require another reviewed same-origin page to link to this manifest HTML path (repeatable)",
    )
    stdout = parser.add_mutually_exclusive_group()
    stdout.add_argument("--json", action="store_true", help="Print both reports as JSON")
    parser.add_argument(
        "--json-file", type=Path, metavar="PATH",
        help="Write both reports to a new UTF-8 JSON file; never overwrite",
    )
    stdout.add_argument(
        "--junit", action="store_true",
        help="Print a JUnit XML suite of blocking HTML and sitemap findings",
    )
    parser.add_argument(
        "--junit-file", type=Path, metavar="PATH",
        help="Write JUnit XML to a new file outside the build; combines with --json-file",
    )
    parser.add_argument(
        "--markdown-file", type=Path, metavar="PATH",
        help="Write a bounded Markdown CI summary to a new file outside the build",
    )
    parser.add_argument(
        "--sarif-file", type=Path, metavar="PATH",
        help="Write combined HTML and sitemap findings as new UTF-8 SARIF 2.1.0",
    )
    parser.add_argument(
        "--sarif-source-map", type=Path, metavar="PATH",
        help="Reviewed local build-to-source hints for HTML SARIF findings only",
    )
    args = parser.parse_args(argv)
    if args.sarif_source_map and not args.sarif_file:
        parser.error("--sarif-source-map requires --sarif-file")
    if (args.json or args.junit) and (
        args.json_file or args.junit_file or args.markdown_file or args.sarif_file
    ):
        parser.error("stdout report mode is not allowed with file report modes")

    try:
        reviewed_manifest = read_manifest(args.manifest)
        site = verify_manifest(
            reviewed_manifest,
            args.build_dir,
            require_full_coverage=args.require_full_coverage,
            check_hreflang_reciprocity=args.check_hreflang_reciprocity,
            check_hreflang_clusters=args.check_hreflang_clusters,
            check_canonical_consistency=args.check_canonical_consistency,
            check_reviewed_navigation=args.check_reviewed_navigation,
            check_reviewed_fragments=args.check_reviewed_fragments,
            check_unique_titles=args.check_unique_titles,
            check_unique_descriptions=args.check_unique_descriptions,
            required_inbound_paths=tuple(args.require_inbound_link),
        )
        sitemap = verify_sitemap_manifest(
            reviewed_manifest, args.sitemaps,
            require_indexability_coverage=args.require_indexability_coverage,
            reject_noncanonical_urls=args.reject_noncanonical_urls,
            reject_unreviewed_urls=args.reject_unreviewed_urls,
        )
        sources = (
            read_source_map(args.sarif_source_map, site, source_root=Path.cwd())
            if args.sarif_source_map else {}
        )
    except (OSError, UnicodeError, ValueError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
        return 2

    status = "pass" if site["status"] == sitemap["status"] == "pass" else "fail"
    if args.json_file or args.junit_file or args.markdown_file or args.sarif_file:
        reports: list[tuple[Path, str, str]] = []
        if args.json_file:
            reports.append((args.json_file, "JSON", json.dumps(
                {"status": status, "site": site, "sitemap": sitemap},
                indent=2, sort_keys=True,
            )))
        if args.junit_file:
            reports.append((args.junit_file, "JUnit", build_release_junit(site, sitemap)))
        if args.markdown_file:
            reports.append((args.markdown_file, "Markdown", build_release_markdown(
                site, sitemap,
            )))
        if args.sarif_file:
            reports.append((args.sarif_file, "SARIF", json.dumps(
                build_release_sarif(site, sitemap, args.manifest, sources=sources),
                indent=2, sort_keys=True,
            )))
        try:
            # Preflight every target before writing either report. The second
            # artifact must not invalidate or overwrite the first one.
            build_root = args.build_dir.resolve()
            targets = [path.resolve() for path, _, _ in reports]
            if len(set(targets)) != len(targets):
                raise ValueError("All report destinations must be distinct files")
            for (path, label, _), target in zip(reports, targets, strict=True):
                if target.is_relative_to(build_root):
                    raise ValueError(f"{label} report must be outside the build directory")
                if path.exists() or path.is_symlink():
                    raise FileExistsError(f"{label} report already exists: {path}")
                # A missing or non-directory parent would fail only after
                # earlier reports had already been created. Reject it up front.
                if not path.parent.is_dir():
                    raise ValueError(
                        f"{label} report parent must be an existing directory: {path.parent}"
                    )
            for path, _, payload in reports:
                # Exclusive creation still protects against a post-preflight race.
                with path.open("x", encoding="utf-8", newline="\n") as output:
                    output.write(payload + "\n")
        except (OSError, UnicodeError, ValueError) as error:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
            return 2
    elif args.junit:
        print(build_release_junit(site, sitemap))
    elif args.json:
        serialized = json.dumps(
            {"status": status, "site": site, "sitemap": sitemap},
            indent=2, sort_keys=True,
        )
        print(serialized)
    else:
        pages = site["summary"]
        xml = sitemap["summary"]
        print(f"{site['status'].upper()} HTML: {pages['passed']}/{pages['checked']} "
              "reviewed pages passed")
        failed_pages: list[str] = []
        for page in site["pages"]:
            if page["status"] == "fail":
                issues = page["issues"]
                codes = ", ".join(
                    _preview(issue["code"]) for issue in issues[:_MAX_CODES_PER_PAGE]
                )
                if len(issues) > _MAX_CODES_PER_PAGE:
                    codes += f" (+{len(issues) - _MAX_CODES_PER_PAGE} more; use --json)"
                failed_pages.append(f"  FAIL {_preview(page['path'])}: {codes}")
        unlisted = site["coverage"]["unlisted"]
        if unlisted:
            print(f"  Unlisted built HTML: {len(unlisted)} "
                  "(use --json for exact paths)")
        # Preserve a strict coverage example even when many failed pages fill
        # the console; optional unlisted pages remain a count, not failures.
        coverage_lines = (
            [f"  Unlisted HTML: {_preview(path)}" for path in unlisted]
            if site["coverage"]["require_full_coverage"] else []
        )
        html_lines, hidden_html = _bounded_human_lines((failed_pages, coverage_lines))
        for line in html_lines:
            print(line)
        if hidden_html:
            print(f"  ... and {hidden_html} more HTML findings; use --json for all")
        print(f"{sitemap['status'].upper()} sitemap: {xml['required']} required, "
              f"{xml['excluded']} excluded, {xml['sitemap_urls']} URLs examined")
        sitemap_groups = (
            [f"  Missing required: {_preview(url)}" for url in sitemap["missing_required"]],
            [f"  Unexpected noindex: {_preview(url)}" for url in sitemap["present_excluded"]],
            [f"  Noncanonical reviewed sitemap URL: {_preview(url)}"
             for url in sitemap.get("present_noncanonical", [])],
            [f"  Unreviewed sitemap URL: {_preview(url)}"
             for url in sitemap.get("unreviewed_urls", [])],
        )
        sitemap_lines, hidden_sitemap = _bounded_human_lines(sitemap_groups)
        for line in sitemap_lines:
            print(line)
        if hidden_sitemap:
            print(f"  ... and {hidden_sitemap} more sitemap findings; use --json for all")
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
