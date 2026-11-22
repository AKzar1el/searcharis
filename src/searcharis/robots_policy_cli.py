"""Fail closed on unreviewed built robots.txt drift, without fetching any URL.

Default behavior is exact artifact-byte parity. An optional, narrow root-crawl
guard inspects explicit reviewed rules without evaluating arbitrary paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from searcharis.cli_error_display import human_input_error
from searcharis.contract_ci import _is_canonical_url
from searcharis.robots_markdown import build_current_robots_markdown
from searcharis.site_junit import build_current_robots_junit

_MAX_POLICY_BYTES = 512_000
_MAX_REQUIRED_SITEMAPS = 16


def format_github_annotations(report: dict) -> list[str]:
    """Report only blocking reviewed-policy failures, with no invented file location."""
    if report["status"] != "fail":
        return []
    if report["reason"] == "sitemap_declaration_missing":
        missing = report.get("missing_sitemap_urls") or [report["required_sitemap_url"]]
        messages = [f"Missing reviewed Sitemap: {url!r}" for url in missing[:16]]
    elif report["reason"] == "root_crawl_block":
        messages = ["Reviewed robots.txt explicitly blocks Googlebot at site root"]
    else:
        messages = [f"Reviewed robots.txt {report['reason']}"]
    annotations: list[str] = []
    for message in messages:
        bounded = message if len(message) <= 160 else message[:157] + "..."
        escaped = (
            bounded.replace("%", "%25").replace("\r", "%0D")
            .replace("\n", "%0A").replace("::", ": :")
            .replace("\u2028", " ").replace("\u2029", " ")
        )
        annotations.append(f"::error title=SEO robots.txt::{escaped}")
    return annotations


def _read_bounded(path: Path) -> bytes:
    with path.open("rb") as source:
        data = source.read(_MAX_POLICY_BYTES + 1)
    if len(data) > _MAX_POLICY_BYTES:
        raise ValueError(f"robots.txt policy exceeds {_MAX_POLICY_BYTES}-byte limit: {path}")
    return data


def _declares_sitemap(policy: bytes, required_url: str) -> bool:
    """Find a reviewed Sitemap directive, not a comment or a URL substring."""
    for line in policy.decode("utf-8-sig").splitlines():
        active = line.partition("#")[0].strip()
        name, separator, value = active.partition(":")
        if separator and name.strip().casefold() == "sitemap" and value.strip() == required_url:
            return True
    return False


def _explicit_googlebot_root_block(policy: bytes) -> bool:
    """Detect a basic Googlebot root block, not arbitrary REP path matching."""
    groups: list[tuple[tuple[str, ...], tuple[tuple[str, str], ...]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []
    has_rule = False

    for raw_line in policy.decode("utf-8-sig").splitlines():
        active = raw_line.partition("#")[0].strip()
        name, colon, value = active.partition(":")
        if not colon:
            continue
        name, value = name.strip().casefold(), value.strip()
        if name == "user-agent":
            if has_rule:
                groups.append((tuple(agents), tuple(rules)))
                agents, rules, has_rule = [], [], False
            if value:
                # Google treats `googlebot*` and `googlebot/1.2` as Googlebot.
                agent = value.casefold().split()[0].split("/", 1)[0]
                if agent != "*":
                    agent = agent.rstrip("*")
                agents.append(agent)
        elif name in {"allow", "disallow"} and agents:
            has_rule = True
            rules.append((name, value))
    if agents:
        groups.append((tuple(agents), tuple(rules)))

    # Googlebot-specific groups override wildcard groups, not combine with them.
    specific = [rules for agents, rules in groups if "googlebot" in agents]
    selected = specific if specific else [
        rules for agents, rules in groups if "*" in agents
    ]
    applicable = [rule for group in selected for rule in group]
    # An anchored /$ blocks only the homepage, but is more specific than
    # a plain Allow: / (or its equivalent trailing-wildcard spelling).
    if any(name == "disallow" and value == "/$" for name, value in applicable):
        return not any(
            name == "allow" and value in {"/$", "/*$"}
            for name, value in applicable
        )
    if not any(name == "disallow" and value == "/" for name, value in applicable):
        return False
    # Only these simple allow rules can explicitly admit the homepage itself.
    return not any(
        name == "allow" and value in {"/", "/$", "/*", "/*$"}
        for name, value in applicable
    )


def verify_reviewed_robots_policy(
    reviewed_policy: Path, build_dir: Path,
    *, required_sitemap_url: str | Sequence[str] | None = None,
    check_root_crawl_block: bool = False,
) -> dict:
    """Compare reviewed source policy to the exact build-root robots.txt artifact."""
    if required_sitemap_url is None:
        required_urls: tuple[str, ...] = ()
    elif isinstance(required_sitemap_url, str):
        required_urls = (required_sitemap_url,)
    elif isinstance(required_sitemap_url, Sequence):
        required_urls = tuple(required_sitemap_url)
    else:
        raise ValueError("Required robots Sitemap URLs must be a sequence of URL strings")
    if required_sitemap_url is not None and not 1 <= len(required_urls) <= _MAX_REQUIRED_SITEMAPS:
        raise ValueError(f"Require 1 to {_MAX_REQUIRED_SITEMAPS} robots Sitemap URLs")
    if any(
        not isinstance(url, str) or len(url) >= 2048 or not _is_canonical_url(url)
        for url in required_urls
    ):
        raise ValueError("Required robots Sitemap URL must be an absolute fragment-free HTTP(S) URL")
    if len(set(required_urls)) != len(required_urls):
        raise ValueError("Duplicate required robots Sitemap URL")
    root = build_dir.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Static build root must be a directory: {build_dir}")

    expected = reviewed_policy.resolve(strict=True)
    if not expected.is_file() or expected.is_relative_to(root):
        raise ValueError("Reviewed robots policy must be a file outside the static build")
    reviewed_bytes = _read_bounded(expected)
    expected_digest = hashlib.sha256(reviewed_bytes).hexdigest()

    # NTFS often resolves Robots.txt as robots.txt, while public HTTP paths
    # and many Linux CI/build hosts are case-sensitive. Require exact casing.
    built = next((item for item in root.iterdir() if item.name == "robots.txt"), None)
    if built is not None and built.is_symlink():
        raise ValueError("Built robots.txt must be a regular file, not a symlink")
    if built is not None and not built.is_file():
        raise ValueError("Built robots.txt must be a regular file")
    observed_digest = (
        hashlib.sha256(_read_bounded(built)).hexdigest() if built is not None else None
    )
    matches = observed_digest == expected_digest
    missing_sitemaps = [
        url for url in required_urls if not _declares_sitemap(reviewed_bytes, url)
    ]
    sitemap_declared = not missing_sitemaps
    root_blocked = (
        matches and sitemap_declared and check_root_crawl_block
        and _explicit_googlebot_root_block(reviewed_bytes)
    )
    if observed_digest is None:
        reason = "missing"
    elif not matches:
        reason = "changed"
    elif not sitemap_declared:
        reason = "sitemap_declaration_missing"
    elif root_blocked:
        reason = "root_crawl_block"
    else:
        reason = None
    result = {
        "status": "pass" if matches and sitemap_declared and not root_blocked else "fail",
        "artifact": "robots.txt",
        "reason": reason,
        "expected_sha256": expected_digest,
        "observed_sha256": observed_digest,
    }
    if required_urls:
        # Keep the one-URL report stable for existing downstream CI consumers.
        result["required_sitemap_url"] = required_urls[0]
    if len(required_urls) > 1:
        result["required_sitemap_urls"] = list(required_urls)
        result["missing_sitemap_urls"] = missing_sitemaps
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare a reviewed robots.txt policy with the local static build (no URL fetch)",
    )
    parser.add_argument("reviewed_policy", type=Path, help="Reviewed robots.txt outside the build")
    parser.add_argument("build_dir", type=Path, help="Local static build with root robots.txt")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Emit a structured report")
    output.add_argument(
        "--json-file", type=Path, metavar="PATH",
        help="Write complete JSON to a new UTF-8 file instead of stdout; never overwrite",
    )
    parser.add_argument(
        "--junit-file", type=Path, metavar="PATH",
        help="Write current blocking robots findings to a new UTF-8 JUnit XML file",
    )
    parser.add_argument(
        "--markdown-file", type=Path, metavar="PATH",
        help="Write escaped bounded reviewer Markdown to a new UTF-8 file",
    )
    parser.add_argument(
        "--report-dir", type=Path, metavar="DIR",
        help="Write current robots JSON, JUnit and Markdown to an existing directory",
    )
    parser.add_argument(
        "--github-annotations", action="store_true",
        help="Emit escaped GitHub Actions errors for current blocking robots findings",
    )
    parser.add_argument(
        "--require-robots-sitemap", action="append", metavar="URL",
        help="Require a reviewed Sitemap URL (repeatable, up to 16; no URL fetch)",
    )
    parser.add_argument(
        "--check-root-crawl-block", action="store_true",
        help="Flag basic reviewed Googlebot Disallow: / blocking the static site root",
    )
    args = parser.parse_args(argv)
    if args.report_dir:
        if any((args.json, args.json_file, args.junit_file, args.markdown_file)):
            parser.error("--report-dir is not allowed with other report output modes")
        args.json_file = args.report_dir / "seo-robots.json"
        args.junit_file = args.report_dir / "seo-robots.xml"
        args.markdown_file = args.report_dir / "seo-robots.md"
    if args.json and args.github_annotations:
        parser.error("--github-annotations cannot be combined with JSON stdout")
    if args.json and args.junit_file:
        parser.error("--junit-file cannot be combined with JSON stdout")
    if args.json and args.markdown_file:
        parser.error("--markdown-file cannot be combined with JSON stdout")

    try:
        report = verify_reviewed_robots_policy(
            args.reviewed_policy, args.build_dir,
            required_sitemap_url=args.require_robots_sitemap,
            check_root_crawl_block=args.check_root_crawl_block,
        )
    except (OSError, ValueError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
        return 2

    if args.json_file or args.junit_file or args.markdown_file:
        created_outputs: list[tuple[Path, tuple[int, int] | None]] = []
        reports: list[tuple[Path, str, str]] = []
        try:
            # Render all payloads before creating any report.
            if args.json_file:
                reports.append((
                    args.json_file, "JSON", json.dumps(report, indent=2, sort_keys=True),
                ))
            if args.junit_file:
                reports.append((
                    args.junit_file, "JUnit", build_current_robots_junit(report),
                ))
            if args.markdown_file:
                reports.append((
                    args.markdown_file, "Markdown", build_current_robots_markdown(report),
                ))
            targets = [path.resolve() for path, _, _ in reports]
            if len(set(targets)) != len(targets):
                raise ValueError("All report destinations must be distinct files")
            root = args.build_dir.resolve(strict=True)
            for (path, label, _), target in zip(reports, targets, strict=True):
                if target.is_relative_to(root):
                    raise ValueError(f"{label} report must be outside the static build")
                if path.exists() or path.is_symlink():
                    raise FileExistsError(f"{label} report already exists: {path}")
                if not path.parent.is_dir():
                    raise ValueError(
                        f"{label} report parent must be an existing directory: {path.parent}"
                    )
            for path, _, payload in reports:
                # Exclusive creation prevents concurrent CI jobs from overwriting reports.
                with path.open("x", encoding="utf-8", newline="\n") as artifact:
                    created_outputs.append((path, None))
                    info = os.fstat(artifact.fileno())
                    created_outputs[-1] = (path, (info.st_dev, info.st_ino))
                    artifact.write(payload + "\n")
        except (OSError, UnicodeError, ValueError) as error:
            cleanup_errors: list[OSError] = []
            cleanup_skipped = 0
            for path, created_identity in reversed(created_outputs):
                try:
                    current = path.lstat()
                    if (
                        created_identity is None or not created_identity[1]
                        or not current.st_ino
                        or (current.st_dev, current.st_ino) != created_identity
                    ):
                        # A foreign replacement is not ours to delete.
                        cleanup_skipped += 1
                    else:
                        path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as cleanup_error:
                    cleanup_errors.append(cleanup_error)
            cleanup = ""
            if cleanup_skipped:
                cleanup += (
                    f"; partial report cleanup skipped ({cleanup_skipped}): "
                    "report identity changed or unavailable"
                )
            if cleanup_errors:
                cleanup += (
                    f"; partial report cleanup failed ({len(cleanup_errors)}): "
                    f"{human_input_error(cleanup_errors[0])}"
                )
            print(f"Invalid input: {human_input_error(error)}{cleanup}", file=sys.stderr)
            return 2
        if args.github_annotations:
            for line in format_github_annotations(report):
                print(line)
    elif args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.github_annotations:
        for line in format_github_annotations(report):
            print(line)
    elif report["status"] == "pass":
        print(f"PASS robots.txt matches reviewed SHA-256 {report['expected_sha256'][:12]}")
    elif report["reason"] == "sitemap_declaration_missing":
        missing = report.get("missing_sitemap_urls") or [report["required_sitemap_url"]]
        samples = ", ".join(
            human_input_error(ValueError(url), limit=96) for url in missing[:2]
        )
        rest = f" (+{len(missing) - 2} more)" if len(missing) > 2 else ""
        print(f"FAIL robots.txt missing reviewed Sitemap declaration(s): {samples}{rest}")
    elif report["reason"] == "root_crawl_block":
        print("FAIL robots.txt reviewed rules explicitly block Googlebot at site root")
    else:
        observed = report["observed_sha256"]
        actual = observed[:12] if observed else "missing"
        print(
            f"FAIL robots.txt {report['reason']}: reviewed SHA-256 "
            f"{report['expected_sha256'][:12]}, built SHA-256 {actual}"
        )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
