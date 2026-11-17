"""Offline XML sitemap checks against explicit, reviewed SEO indexability intent."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from searcharis.cli_error_display import human_input_error
from searcharis.comparison_markdown import build_current_sitemap_markdown
from searcharis.site_ci import read_manifest
from searcharis.site_junit import build_current_sitemap_junit
from searcharis.sitemap_ci import verify_sitemap_manifest

_MAX_HUMAN_FINDINGS = 20


def _display(value: str) -> str:
    text = repr(value)
    return text if len(text) <= 140 else text[:137] + "..."


def _bounded_human_lines(groups: tuple[list[str], ...]) -> tuple[list[str], int]:
    """Reserve an example per populated sitemap failure category in CI logs."""
    selected: list[str] = []
    for index, group in enumerate(groups):
        reserved = sum(bool(later) for later in groups[index + 1:])
        available = max(0, _MAX_HUMAN_FINDINGS - len(selected) - reserved)
        selected.extend(group[:available])
    return selected, sum(map(len, groups)) - len(selected)


def format_github_annotations(report: dict) -> list[str]:
    """Emit bounded current-build failures, without inventing XML source locations."""
    groups = (
        ("Missing required", report["missing_required"]),
        ("Noindex URL", report["present_excluded"]),
        ("Noncanonical URL", report.get("present_noncanonical", [])),
        ("Unreviewed URL", report.get("unreviewed_urls", [])),
    )
    annotations: list[str] = []
    for index, (title, urls) in enumerate(groups):
        # Preserve at least one slot for every other populated blocking category.
        reserved = sum(bool(other) for _, other in groups[index + 1:])
        take = max(0, _MAX_HUMAN_FINDINGS - len(annotations) - reserved)
        for url in urls[:take]:
            message = _display(url)
            message = (
                message.replace("%", "%25").replace("\r", "%0D")
                .replace("\n", "%0A").replace("::", ": :")
                .replace("\u2028", " ").replace("\u2029", " ")
            )
            annotations.append(f"::error title=Sitemap {title}::{message}")
    return annotations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check local XML sitemap against reviewed indexability assertions",
    )
    parser.add_argument("manifest", type=Path, help="Reviewed SEO page manifest (version 1)")
    parser.add_argument(
        "sitemaps", nargs="*", type=Path,
        help="Explicit local sitemap XML URL sets, or use --sitemap-index",
    )
    parser.add_argument(
        "--sitemap-index", type=Path, metavar="PATH",
        help="Read 1-16 XML/XML.gz shards beside a local sitemap index, no URL fetch",
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
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Print structured JSON outcome")
    output.add_argument(
        "--json-file", type=Path, metavar="PATH",
        help="Write complete JSON to a new UTF-8 file instead of stdout; never overwrite",
    )
    parser.add_argument(
        "--junit-file", type=Path, metavar="PATH",
        help="Write current blocking sitemap URL findings as a create-only JUnit XML file",
    )
    parser.add_argument(
        "--markdown-file", type=Path, metavar="PATH",
        help="Write bounded HTML-escaped current-sitemap reviewer Markdown to a new file",
    )
    parser.add_argument(
        "--report-dir", type=Path, metavar="DIR",
        help="Create JSON, JUnit and Markdown current-sitemap reports in an existing directory",
    )
    parser.add_argument(
        "--github-annotations", action="store_true",
        help="Emit bounded current sitemap errors for GitHub Actions on stdout",
    )
    args = parser.parse_args(argv)
    if args.report_dir:
        if any((args.json, args.json_file, args.junit_file, args.markdown_file)):
            parser.error("--report-dir is not allowed with other report output modes")
        args.json_file = args.report_dir / "seo-sitemap.json"
        args.junit_file = args.report_dir / "seo-sitemap.xml"
        args.markdown_file = args.report_dir / "seo-sitemap.md"
    if args.json and args.github_annotations:
        parser.error("--github-annotations cannot be combined with JSON stdout")
    if args.json and args.junit_file:
        parser.error("--junit-file cannot be combined with JSON stdout")
    if args.json and args.markdown_file:
        parser.error("--markdown-file cannot be combined with JSON stdout")

    try:
        report = verify_sitemap_manifest(
            read_manifest(args.manifest), args.sitemaps,
            require_indexability_coverage=args.require_indexability_coverage,
            reject_noncanonical_urls=args.reject_noncanonical_urls,
            reject_unreviewed_urls=args.reject_unreviewed_urls,
            sitemap_index_path=args.sitemap_index,
        )
    except (OSError, UnicodeError, ValueError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
        return 2

    if args.json_file or args.junit_file or args.markdown_file:
        created_outputs: list[tuple[Path, tuple[int, int] | None]] = []
        reports: list[tuple[Path, str, str]] = []
        try:
            # Render before creating files so a serializer failure leaves no output.
            if args.json_file:
                reports.append((
                    args.json_file, "JSON", json.dumps(report, indent=2, sort_keys=True),
                ))
            if args.junit_file:
                reports.append((args.junit_file, "JUnit", build_current_sitemap_junit(report)))
            if args.markdown_file:
                reports.append((
                    args.markdown_file, "Markdown", build_current_sitemap_markdown(report),
                ))
            targets = [path.resolve() for path, _, _ in reports]
            if len(set(targets)) != len(targets):
                raise ValueError("All report destinations must be distinct files")
            # Reports must never mutate local sitemap artifacts being verified.
            sources = [args.sitemap_index] if args.sitemap_index else args.sitemaps
            roots = [source.parent.resolve(strict=True) for source in sources]
            for (path, label, _), target in zip(reports, targets, strict=True):
                if any(target.is_relative_to(root) for root in roots):
                    raise ValueError(f"{label} report must be outside the supplied sitemap directory")
                if path.exists() or path.is_symlink():
                    raise FileExistsError(f"{label} report already exists: {path}")
                if not path.parent.is_dir():
                    raise ValueError(
                        f"{label} report parent must be an existing directory: {path.parent}"
                    )
            for path, _, payload in reports:
                # Exclusive creation still protects against races after preflight.
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
                    # Never unlink a report replaced by another process.
                    if (
                        identity is None or not identity[1]
                        or not current.st_ino
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
            for line in format_github_annotations(report):
                print(line)
    elif args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.github_annotations:
        for line in format_github_annotations(report):
            print(line)
    else:
        summary = report["summary"]
        print(
            f"{report['status'].upper()} {summary['required']} required, "
            f"{summary['excluded']} excluded, "
            f"{summary['unasserted_pages']} without indexability assertion, "
            f"{summary['sitemap_urls']} sitemap URLs"
        )
        groups = (
            [f"  Missing required: {_display(url)}" for url in report["missing_required"]],
            [f"  Unexpected noindex URL: {_display(url)}" for url in report["present_excluded"]],
            [f"  Noncanonical reviewed URL in sitemap: {_display(url)}"
             for url in report.get("present_noncanonical", [])],
            [f"  Unreviewed sitemap URL: {_display(url)}"
             for url in report.get("unreviewed_urls", [])],
        )
        lines, omitted = _bounded_human_lines(groups)
        for line in lines:
            print(line)
        if omitted:
            print(f"  ... and {omitted} more sitemap findings; use --json for all")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
