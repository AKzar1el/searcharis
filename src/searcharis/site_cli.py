"""Run offline SEO checks over an explicit manifest of built HTML files."""

from __future__ import annotations

import argparse
import json
import os
import sys
from itertools import islice
from pathlib import Path

from searcharis.cli_error_display import human_input_error
from searcharis.site_ci import read_manifest, verify_manifest
from searcharis.site_junit import build_site_junit
from searcharis.site_sarif import build_sarif_report
from searcharis.site_source_map import read_source_map

_MAX_UNLISTED_DISPLAY = 20
_MAX_FAILED_PAGE_DISPLAY = 20
_MAX_ISSUES_PER_PAGE = 8
_MAX_GITHUB_ANNOTATIONS = 20


def _label(value: str, *, limit: int = 120) -> str:
    # Built artifact names and issue codes are untrusted CI log content.
    display = repr(value)[1:-1]
    return display if len(display) <= limit else display[:limit - 3] + "..."


def _preview(value: object, *, limit: int = 120) -> str:
    display = repr(value)
    return display if len(display) <= limit else display[:limit - 3] + "..."


def format_github_annotations(report: dict) -> list[str]:
    """Annotate only blocking reviewed-site findings, without guessed source lines."""
    page_issues = list(islice(
        ((page, issue) for page in report["pages"] for issue in page["issues"]),
        _MAX_GITHUB_ANNOTATIONS,
    ))
    unlisted = (
        report["coverage"]["unlisted"]
        if report["coverage"]["require_full_coverage"] else []
    )
    groups = [("page issue", page_issues), ("unlisted HTML", unlisted)]
    commands: list[str] = []
    for index, (kind, items) in enumerate(groups):
        # Preserve room for a strict-coverage failure even with many page issues.
        reserved = sum(bool(other) for _, other in groups[index + 1:])
        take = max(0, _MAX_GITHUB_ANNOTATIONS - len(commands) - reserved)
        for item in items[:take]:
            if kind == "page issue":
                page, issue = item
                message = (
                    f"{_preview(page['path'])} ({_preview(page['checked_url'])}): "
                    f"{_preview(issue['code'])}; expected {_preview(issue['expected'])}, "
                    f"observed {_preview(issue['actual'])}"
                )
            else:
                message = f"Unreviewed built HTML: {_preview(item)}"
            # Built paths do not identify repository source lines. Escaping also
            # prevents untrusted contract values from injecting workflow commands.
            message = (
                message.replace("%", "%25").replace("\r", "%0D")
                .replace("\n", "%0A").replace("::", ": :")
            )
            commands.append(f"::error title=SEO {kind}::{message}")
    return commands


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
        "--check-internal-nofollow", action="store_true",
        help="Fail if links to reviewed same-origin pages carry rel=nofollow (opt-in)",
    )
    parser.add_argument(
        "--check-reviewed-fragments", action="store_true",
        help="Fail if links to reviewed same-origin pages use missing HTML fragment IDs",
    )
    parser.add_argument(
        "--check-unique-titles", action="store_true",
        help="Fail on duplicate titles across reviewed same-origin indexable pages",
    )
    parser.add_argument(
        "--check-unique-descriptions", action="store_true",
        help="Fail on duplicate descriptions across reviewed same-origin indexable pages",
    )
    parser.add_argument(
        "--require-inbound-link", action="append", default=[], metavar="PAGE_PATH",
        help="Require another reviewed same-origin page to link to this manifest HTML path (repeatable)",
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
    output.add_argument(
        "--sarif-file", type=Path, metavar="PATH",
        help="Write SARIF 2.1.0 to a new UTF-8 file; never overwrite existing files",
    )
    output.add_argument(
        "--junit-file", type=Path, metavar="PATH",
        help="Write standalone reviewed-site JUnit XML to a new file for CI test reports",
    )
    parser.add_argument(
        "--report-dir", type=Path, metavar="DIR",
        help="Create JSON, JUnit and SARIF site reports in an existing directory",
    )
    parser.add_argument(
        "--github-annotations", action="store_true",
        help="Print bounded GitHub Actions error commands for blocking built-site checks",
    )
    parser.add_argument(
        "--sarif-source-map",
        type=Path,
        help="Optional JSON map of built HTML to existing repository source files (SARIF only)",
    )
    args = parser.parse_args(argv)
    if args.report_dir:
        if any((args.json, args.sarif, args.json_file, args.sarif_file, args.junit_file)):
            parser.error("--report-dir is not allowed with other report output modes")
        args.json_file = args.report_dir / "seo-site.json"
        args.junit_file = args.report_dir / "seo-site.xml"
        args.sarif_file = args.report_dir / "seo-site.sarif"
    if args.github_annotations and (args.json or args.sarif):
        parser.error("--github-annotations cannot be combined with JSON or SARIF stdout")
    if args.sarif_source_map and not (args.sarif or args.sarif_file):
        parser.error("--sarif-source-map requires --sarif or --sarif-file")

    try:
        report = verify_manifest(
            read_manifest(args.manifest),
            args.build_dir,
            require_full_coverage=args.require_full_coverage,
            check_hreflang_reciprocity=args.check_hreflang_reciprocity,
            check_hreflang_clusters=args.check_hreflang_clusters,
            check_canonical_consistency=args.check_canonical_consistency,
            check_reviewed_navigation=args.check_reviewed_navigation,
            check_internal_nofollow=args.check_internal_nofollow,
            check_reviewed_fragments=args.check_reviewed_fragments,
            check_unique_titles=args.check_unique_titles,
            check_unique_descriptions=args.check_unique_descriptions,
            required_inbound_paths=tuple(args.require_inbound_link),
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

    if args.json_file or args.sarif_file or args.junit_file:
        created_outputs: list[tuple[Path, tuple[int, int] | None]] = []
        reports: list[tuple[Path, str, str]] = []
        try:
            if args.json_file:
                reports.append((
                    args.json_file, "JSON", json.dumps(report, indent=2, sort_keys=True),
                ))
            if args.junit_file:
                reports.append((args.junit_file, "JUnit", build_site_junit(report)))
            if args.sarif_file:
                reports.append((
                    args.sarif_file, "SARIF", json.dumps(
                        build_sarif_report(report, sources=sources), indent=2,
                        sort_keys=True,
                    ),
                ))
        except (OSError, UnicodeError, ValueError) as error:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
            return 2
        try:
            build_root = args.build_dir.resolve()
            targets = [path.resolve() for path, _, _ in reports]
            if len(set(targets)) != len(targets):
                raise ValueError("All report destinations must be distinct files")
            # Preflight the entire bundle before writing the first report.
            for (path, label, _), target in zip(reports, targets, strict=True):
                if target.is_relative_to(build_root):
                    raise ValueError(f"{label} report must be outside the build directory")
                if path.exists() or path.is_symlink():
                    raise FileExistsError(f"{label} report already exists: {path}")
                if not path.parent.is_dir():
                    raise ValueError(
                        f"{label} report parent must be an existing directory: {path.parent}"
                    )
            for path, _, payload in reports:
                # Exclusive creation still protects against post-preflight races.
                with path.open("x", encoding="utf-8", newline="\n") as output_file:
                    created_outputs.append((path, None))
                    info = os.fstat(output_file.fileno())
                    created_outputs[-1] = (path, (info.st_dev, info.st_ino))
                    output_file.write(payload + "\n")
        except (OSError, UnicodeError, ValueError) as error:
            cleanup_errors: list[OSError] = []
            cleanup_skipped = 0
            for path, identity in reversed(created_outputs):
                try:
                    current = path.lstat()
                    # Another job may have replaced our report after creation.
                    if (
                        identity is None or not identity[1] or not current.st_ino
                        or (current.st_dev, current.st_ino) != identity
                    ):
                        cleanup_skipped += 1
                        continue
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as cleanup_error:
                    cleanup_errors.append(cleanup_error)
            cleanup_message = ""
            if cleanup_skipped:
                cleanup_message += (
                    f"; partial report cleanup skipped ({cleanup_skipped}): "
                    "report identity changed or unavailable"
                )
            if cleanup_errors:
                cleanup_message += (
                    f"; partial report cleanup failed ({len(cleanup_errors)}): "
                    f"{human_input_error(cleanup_errors[0])}"
                )
            print(
                f"Invalid input: {human_input_error(error)}{cleanup_message}",
                file=sys.stderr,
            )
            return 2
        if args.github_annotations:
            # Emit commands only after the requested report was saved successfully.
            for line in format_github_annotations(report):
                print(line)
    elif args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.sarif:
        print(json.dumps(build_sarif_report(report, sources=sources), indent=2, sort_keys=True))
    elif args.github_annotations:
        for line in format_github_annotations(report):
            print(line)
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
        if args.check_internal_nofollow:
            links = report["internal_nofollow"]
            print(
                f"  Internal nofollow: {links['checked_links']} local links checked, "
                f"{len(links['nofollow_targets'])} restricted targets"
            )
        if args.check_reviewed_fragments:
            fragments = report["reviewed_fragments"]
            print(
                f"  Fragments: {fragments['checked_fragments']} reviewed targets checked, "
                f"{len(fragments['missing_targets'])} missing IDs, "
                f"{len(fragments['unverified_targets'])} outside-manifest targets"
            )
        if args.require_inbound_link:
            inbound = report["reviewed_inbound"]
            print(
                f"  Inbound links: {len(inbound['linked_paths'])}/"
                f"{len(inbound['required_paths'])} critical pages linked from a reviewed peer"
            )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
