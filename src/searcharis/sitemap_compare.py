"""Gate only newly introduced sitemap regressions between two local index builds.

Both snapshots and the reviewed manifest are local inputs. Public URLs in XML
are identifiers only: the comparator never fetches or resolves them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from searcharis.cli_error_display import human_input_error
from searcharis.comparison_markdown import build_sitemap_comparison_markdown
from searcharis.site_ci import _page_url_identity, read_manifest
from searcharis.site_junit import build_sitemap_comparison_junit
from searcharis.sitemap_ci import (
    _local_index_shards,
    _read_sitemaps,
    verify_sitemap_manifest,
)

_DEFAULT_CATEGORIES = ("missing_required", "present_excluded")
_MAX_HUMAN_URLS = 20


def _check_independent_snapshots(
    baseline_index: Path, candidate_index: Path, *, single_sitemap: bool = False,
) -> None:
    """Reject directory aliasing and shared NTFS/POSIX file identities."""
    baseline_root = baseline_index.parent.resolve(strict=True)
    candidate_root = candidate_index.parent.resolve(strict=True)
    if (
        baseline_root == candidate_root
        or baseline_root.is_relative_to(candidate_root)
        or candidate_root.is_relative_to(baseline_root)
    ):
        raise ValueError("Baseline and candidate need non-overlapping sitemap directories")

    if single_sitemap:
        for path in (baseline_index, candidate_index):
            if not path.name.lower().endswith((".xml", ".xml.gz")):
                raise ValueError("Single sitemap must have an .xml or .xml.gz filename")
            if path.is_symlink() or not path.is_file():
                raise ValueError("Single sitemap must be a regular local file")
            if path.name not in {item.name for item in path.parent.iterdir()}:
                raise ValueError("Single sitemap path must match the exact-case filename")
        baseline_urls = _read_sitemaps(baseline_index)
        candidate_urls = _read_sitemaps(candidate_index)
        # A trusted empty baseline has no public site to bind a candidate to.
        # Fail closed rather than silently inheriting all missing reviewed URLs
        # when the candidate points at an unrelated site.
        if not baseline_urls:
            raise ValueError("Baseline single sitemap must establish a nonempty site identity")
        if (
            candidate_urls
            and next(iter(baseline_urls))[:3] != next(iter(candidate_urls))[:3]
        ):
            raise ValueError("Baseline and candidate sitemaps must refer to the same site")
        baseline_shards: tuple[Path, ...] = ()
        candidate_shards: tuple[Path, ...] = ()
    else:
        baseline_shards, baseline_site = _local_index_shards(baseline_index)
        candidate_shards, candidate_site = _local_index_shards(candidate_index)
        if baseline_site != candidate_site:
            raise ValueError("Baseline and candidate sitemap indexes must refer to the same site")

    baseline_ids: set[tuple[int, int]] = set()
    for path in (baseline_index, *baseline_shards):
        info = path.stat()
        if info.st_ino:
            baseline_ids.add((info.st_dev, info.st_ino))
    for path in (candidate_index, *candidate_shards):
        info = path.stat()
        if info.st_ino and (info.st_dev, info.st_ino) in baseline_ids:
            raise ValueError("Baseline and candidate share a hard-linked sitemap artifact")


def compare_sitemap_reports(baseline: dict, candidate: dict) -> dict:
    """Compare verified reports using exact reviewed URL identities."""
    categories = tuple(
        category for category in (
            *_DEFAULT_CATEGORIES, "present_noncanonical", "unreviewed_urls",
        ) if category in baseline and category in candidate
    )
    sections: dict[str, dict[str, list[str]]] = {
        "new": {}, "inherited": {}, "resolved": {},
    }
    for category in categories:
        # Equivalent URL spellings must not become a new regression merely
        # because a generator changes percent-encoding or host casing.
        old = {_page_url_identity(url): url for url in baseline[category]}
        current = {_page_url_identity(url): url for url in candidate[category]}
        sections["new"][category] = sorted(
            url for identity, url in current.items() if identity not in old
        )
        sections["inherited"][category] = sorted(
            url for identity, url in current.items() if identity in old
        )
        sections["resolved"][category] = sorted(
            url for identity, url in old.items() if identity not in current
        )

    counts = {name: sum(len(urls) for urls in value.values())
              for name, value in sections.items()}
    return {
        "status": "regression" if counts["new"] else "pass",
        "summary": {
            **counts,
            "baseline_sitemap_urls": baseline["summary"]["sitemap_urls"],
            "candidate_sitemap_urls": candidate["summary"]["sitemap_urls"],
        },
        **sections,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gate new offline sitemap regressions, tolerating inherited debt",
    )
    parser.add_argument("manifest", type=Path, help="Reviewed SEO page manifest")
    parser.add_argument(
        "baseline_index", type=Path, help="Trusted index or single URL-set XML",
    )
    parser.add_argument(
        "candidate_index", type=Path, help="Candidate index or single URL-set XML",
    )
    parser.add_argument(
        "--single-sitemap", action="store_true",
        help="Treat each positional XML/XML.gz input as one local URL set, not a sitemap index",
    )
    parser.add_argument(
        "--reject-noncanonical-urls", action="store_true",
        help="Gate newly introduced reviewed noncanonical sitemap URLs",
    )
    parser.add_argument(
        "--reject-unreviewed-urls", action="store_true",
        help="Gate newly introduced sitemap URLs absent from reviewed manifest",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Print full comparison as JSON")
    output.add_argument(
        "--json-file", type=Path, metavar="PATH",
        help="Write complete JSON to a new UTF-8 file outside both builds",
    )
    output.add_argument(
        "--junit", action="store_true",
        help="Print JUnit XML for only newly blocking sitemap regressions",
    )
    output.add_argument(
        "--junit-file", type=Path, metavar="PATH",
        help="Write regression-only JUnit XML to a new file outside both builds",
    )
    output.add_argument(
        "--markdown", action="store_true",
        help="Print a bounded, reviewer-friendly Markdown regression summary",
    )
    output.add_argument(
        "--markdown-file", type=Path, metavar="PATH",
        help="Write a bounded UTF-8 Markdown summary to a new file outside both builds",
    )
    args = parser.parse_args(argv)

    try:
        _check_independent_snapshots(
            args.baseline_index, args.candidate_index,
            single_sitemap=args.single_sitemap,
        )
        manifest = read_manifest(args.manifest)
        checks = {
            "reject_noncanonical_urls": args.reject_noncanonical_urls,
            "reject_unreviewed_urls": args.reject_unreviewed_urls,
        }
        baseline = verify_sitemap_manifest(
            manifest, args.baseline_index if args.single_sitemap else [],
            sitemap_index_path=None if args.single_sitemap else args.baseline_index,
            **checks,
        )
        candidate = verify_sitemap_manifest(
            manifest, args.candidate_index if args.single_sitemap else [],
            sitemap_index_path=None if args.single_sitemap else args.candidate_index,
            **checks,
        )
        result = compare_sitemap_reports(baseline, candidate)
    except (OSError, ValueError, UnicodeError, RecursionError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
        return 2

    if args.json_file or args.junit_file or args.markdown_file:
        path = args.json_file or args.junit_file or args.markdown_file
        label = "JSON" if args.json_file else "JUnit" if args.junit_file else "Markdown"
        created = False
        try:
            if path.exists() or path.is_symlink():
                raise FileExistsError(f"{label} comparison already exists: {path}")
            if not path.parent.is_dir():
                raise ValueError(
                    f"{label} comparison parent must be an existing directory: {path.parent}"
                )
            # Comparing compiled site artifacts must never add a report to either build.
            output_path = path.resolve()
            if any(
                output_path.is_relative_to(index.parent.resolve(strict=True))
                for index in (args.baseline_index, args.candidate_index)
            ):
                raise ValueError(f"{label} comparison must be outside both sitemap directories")
            with path.open("x", encoding="utf-8", newline="\n") as output_file:
                created = True
                payload = (
                    json.dumps(result, indent=2, sort_keys=True) if args.json_file
                    else build_sitemap_comparison_junit(result) if args.junit_file
                    else build_sitemap_comparison_markdown(result)
                )
                output_file.write(payload + "\n")
        except (OSError, UnicodeError, ValueError) as error:
            if created:
                try:
                    path.unlink()
                except OSError as cleanup_error:
                    print(
                        f"Invalid input: {human_input_error(error)}; "
                        f"partial report cleanup failed: {human_input_error(cleanup_error)}",
                        file=sys.stderr,
                    )
                    return 2
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
            return 2
    elif args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.junit:
        print(build_sitemap_comparison_junit(result))
    elif args.markdown:
        print(build_sitemap_comparison_markdown(result))
    else:
        summary = result["summary"]
        print(
            f"{result['status'].upper()} {summary['new']} new, "
            f"{summary['inherited']} inherited, {summary['resolved']} resolved sitemap findings"
        )
        printed = 0
        for category, urls in result["new"].items():
            for url in urls:
                if printed >= _MAX_HUMAN_URLS:
                    break
                # repr makes control characters visible rather than injecting CI lines.
                label = repr(url)
                print(f"  {category}: {label[:137] + '...' if len(label) > 140 else label}")
                printed += 1
        if summary["new"] > printed:
            print(f"  ... and {summary['new'] - printed} more; use --json for all findings")
    return 1 if result["status"] == "regression" else 0


if __name__ == "__main__":
    sys.exit(main())
