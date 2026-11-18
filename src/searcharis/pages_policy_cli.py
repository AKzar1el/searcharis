"""Verify reviewed Cloudflare Pages _redirects and _headers in one offline gate.

Both source files are immutable reviewed inputs. This checks built bytes, not
Cloudflare routing, Pages Functions, SSR, or live HTTP response behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from searcharis.cli_error_display import human_input_error
from searcharis.headers_policy_cli import verify_reviewed_headers_policy
from searcharis.redirects_policy_cli import verify_reviewed_redirects_policy
from searcharis.site_junit import build_pages_policy_junit


def format_github_annotations(report: dict) -> list[str]:
    """Create bounded workflow errors for failing static artifacts only."""
    if report["status"] != "fail":
        return []
    annotations: list[str] = []
    for item in report["checks"]:
        artifact = item.get("artifact")
        if item.get("status") != "fail" or artifact not in {"_redirects", "_headers"}:
            continue
        if item.get("reason") == "missing":
            message = f"Compiled {artifact} missing from the static build"
        elif item.get("reason") == "limit_exceeded":
            message = f"Reviewed {artifact} exceeds a documented Pages limit: {item['limit_issue']}"
        else:
            message = f"Compiled {artifact} changed from the reviewed reference"
        annotations.append(f"::error title=SEO Pages {artifact}::{message}")
    return annotations


def _format_human_check(item: dict) -> str:
    if item["status"] == "pass":
        return f"PASS {item['artifact']} reviewed SHA-256 {item['expected_sha256']}"
    if item["reason"] == "limit_exceeded":
        return f"FAIL {item['artifact']} Pages limit: {item['limit_issue']}"
    return (
        f"FAIL {item['artifact']} {item['reason']}: "
        f"expected SHA-256 {item['expected_sha256']}, "
        f"observed SHA-256 {item['observed_sha256'] or 'missing'}"
    )


def build_pages_policy_markdown(report: dict) -> str:
    """Summarize only verified local static artifacts in reviewer-safe Markdown."""
    passed = report["status"] == "pass"
    lines = [
        "# Searcharis Pages static policy check",
        "",
        f"**Result: {'PASS' if passed else 'FAIL'}** (supplied local artifacts only)",
        "",
    ]
    for item in report["checks"]:
        artifact = item["artifact"]  # A fixed _redirects/_headers verifier label.
        if item["status"] == "pass":
            lines.append(
                f"- **{artifact}: PASS** - matches reviewed SHA-256 "
                f"`{item['expected_sha256'][:12]}`"
            )
        elif item["reason"] == "limit_exceeded":
            lines.append(f"- **{artifact}: FAIL** - {item['limit_issue']}")
        else:
            lines.append(
                f"- **{artifact}: FAIL** - {item['reason']}; "
                f"reviewed SHA-256 `{item['expected_sha256'][:12]}`, "
                f"built SHA-256 `{(item['observed_sha256'] or 'missing')[:12]}`"
            )
    if passed:
        lines.extend(["", "No static policy drift in the supplied build."])
    else:
        lines.extend([
            "",
            "## Suggested reviewer action",
            "",
            "Rebuild the static site with the approved _redirects and _headers "
            "artifacts, or review and approve intended policy changes first.",
        ])
    lines.extend([
        "",
        "Offline byte comparison only; this does not test deployed routing, "
        "Functions/SSR headers, search indexing or customer outcomes.",
    ])
    return "\n".join(lines) + "\n"


def _basic_pages_limit_issue(reviewed: Path, artifact: str, digest: str) -> str | None:
    """Check published basic text limits, not complete Pages syntax or routing."""
    # The individual byte-parity verifiers already enforce this bounded read.
    with reviewed.open("rb") as source:
        content = source.read(8_500_001)
    if len(content) > 8_500_000:
        raise ValueError(f"Reviewed {artifact} exceeds the local input byte limit")
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError(f"Reviewed {artifact} changed while checking policy limits")
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"Reviewed {artifact} is not UTF-8 text") from error

    limit = 1_000 if artifact == "_redirects" else 2_000
    static_redirects = 0
    dynamic_redirects = 0
    header_rules = 0
    for line_number, line in enumerate(lines, start=1):
        if artifact == "_redirects":
            if not line.strip() or line.startswith("#"):
                continue
            source = line.split()[0]
            if "*" in source or re.search(r":[A-Za-z]\w*", source):
                dynamic_redirects += 1
            else:
                static_redirects += 1
        elif line.strip() and not line.startswith("#") and not line[0].isspace():
            header_rules += 1
        if len(line) > limit:
            return f"line {line_number} has {len(line)} characters; maximum {limit}"

    if artifact == "_redirects":
        if static_redirects > 2_000:
            return f"{static_redirects} static redirect rules; maximum 2000"
        if dynamic_redirects > 100:
            return f"{dynamic_redirects} dynamic redirect rules; maximum 100"
        if static_redirects + dynamic_redirects > 2_100:
            return f"{static_redirects + dynamic_redirects} redirect rules; maximum 2100"
    elif header_rules > 100:
        return f"{header_rules} header rules; maximum 100"
    return None


def verify_reviewed_pages_policies(
    reviewed_redirects: Path, reviewed_headers: Path, build_dir: Path,
    *, check_policy_limits: bool = False,
) -> dict:
    """Check both static policy artifacts even if one is missing or invalid."""
    checks: list[dict] = []
    errors: list[dict] = []
    for artifact, check, reviewed in (
        ("_redirects", verify_reviewed_redirects_policy, reviewed_redirects),
        ("_headers", verify_reviewed_headers_policy, reviewed_headers),
    ):
        try:
            result = check(reviewed, build_dir)
            if check_policy_limits and result["status"] == "pass":
                issue = _basic_pages_limit_issue(
                    reviewed, artifact, result["expected_sha256"],
                )
                if issue:
                    result.update(
                        status="fail", reason="limit_exceeded", limit_issue=issue,
                    )
            checks.append(result)
        except (OSError, ValueError) as error:
            errors.append({"artifact": artifact, "error": str(error)})

    if errors:
        return {"status": "error", "checks": checks, "errors": errors}
    return {
        "status": "fail" if any(item["status"] == "fail" for item in checks) else "pass",
        "checks": checks,
    }


def _write_report_files(
    reports: list[tuple[Path, str, str]], build_dir: Path,
) -> None:
    """Preflight all destinations and create only the files owned by this run."""
    created: list[tuple[Path, tuple[int, int]]] = []
    try:
        build_root = build_dir.resolve(strict=True)
        resolved = [target.resolve() for target, _, _ in reports]
        if len(set(resolved)) != len(resolved):
            raise ValueError("Report destinations must be distinct files")
        for (target, label, _), path in zip(reports, resolved, strict=True):
            if path.is_relative_to(build_root):
                raise ValueError(f"{label} report must be outside the static build")
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"{label} report already exists: {target}")
            if not target.parent.is_dir():
                raise ValueError(
                    f"{label} report parent must be an existing directory: {target.parent}"
                )
        for target, _, payload in reports:
            with target.open("x", encoding="utf-8", newline="\n") as output:
                info = os.fstat(output.fileno())
                created.append((target, (info.st_dev, info.st_ino)))
                output.write(payload + "\n")
    except (OSError, UnicodeError, ValueError) as error:
        cleanup_notes: list[str] = []
        for target, identity in reversed(created):
            try:
                current = target.lstat()
                if (
                    not identity[1]
                    or not current.st_ino
                    or (current.st_dev, current.st_ino) != identity
                ):
                    cleanup_notes.append(
                        "partial report cleanup skipped: identity changed or unavailable"
                    )
                else:
                    target.unlink()
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                cleanup_notes.append(
                    f"partial report cleanup failed: {human_input_error(cleanup_error)}"
                )
        suffix = "".join(f"; {note}" for note in cleanup_notes)
        raise ValueError(f"{human_input_error(error)}{suffix}") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check reviewed static Cloudflare Pages redirects and headers in one offline run",
    )
    parser.add_argument("reviewed_redirects", type=Path, help="Approved _redirects file outside build")
    parser.add_argument("reviewed_headers", type=Path, help="Approved _headers file outside build")
    parser.add_argument("build_dir", type=Path, help="Compiled static Pages build root")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Print one JSON report")
    output.add_argument(
        "--json-file", type=Path, metavar="PATH",
        help="Create a UTF-8 JSON report outside the build without overwriting",
    )
    parser.add_argument(
        "--junit-file", type=Path, metavar="PATH",
        help="Create a JUnit XML report for both artifacts (can combine with --json-file)",
    )
    parser.add_argument(
        "--markdown-file", type=Path, metavar="PATH",
        help="Create an actionable Markdown summary outside the build",
    )
    parser.add_argument(
        "--report-dir", type=Path, metavar="DIR",
        help="Create JSON, JUnit and Markdown in an existing directory",
    )
    parser.add_argument(
        "--github-annotations", action="store_true",
        help="Emit GitHub Actions error annotations for drift (not with --json)",
    )
    parser.add_argument(
        "--check-policy-limits", action="store_true",
        help="Also enforce basic published Pages line and aggregate rule limits (offline)",
    )
    args = parser.parse_args(argv)
    if args.report_dir:
        if any((args.json, args.json_file, args.junit_file, args.markdown_file)):
            parser.error("--report-dir is not allowed with other report output modes")
        args.json_file = args.report_dir / "seo-pages.json"
        args.junit_file = args.report_dir / "seo-pages.xml"
        args.markdown_file = args.report_dir / "seo-pages.md"
    if args.json and args.github_annotations:
        parser.error("--github-annotations cannot be combined with JSON stdout")
    if args.json and (args.junit_file or args.markdown_file):
        parser.error("JSON stdout cannot be combined with file reports")

    report = verify_reviewed_pages_policies(
        args.reviewed_redirects, args.reviewed_headers, args.build_dir,
        check_policy_limits=args.check_policy_limits,
    )
    if report["status"] == "error":
        if args.json:
            print(json.dumps(report, sort_keys=True))
        else:
            for item in report["checks"]:
                print(f"Partial result: {_format_human_check(item)}", file=sys.stderr)
            for error in report["errors"]:
                print(
                    f"Invalid input ({error['artifact']}): {human_input_error(error['error'])}",
                    file=sys.stderr,
                )
        return 2

    if args.json_file or args.junit_file or args.markdown_file:
        try:
            files: list[tuple[Path, str, str]] = []
            if args.json_file:
                files.append((
                    args.json_file, "JSON", json.dumps(report, indent=2, sort_keys=True),
                ))
            if args.junit_file:
                files.append((args.junit_file, "JUnit", build_pages_policy_junit(report)))
            if args.markdown_file:
                files.append((
                    args.markdown_file, "Markdown", build_pages_policy_markdown(report),
                ))
            _write_report_files(files, args.build_dir)
        except (OSError, UnicodeError, ValueError) as error:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
            return 2
    elif args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        for item in report["checks"]:
            print(_format_human_check(item))
    if args.github_annotations:
        for annotation in format_github_annotations(report):
            print(annotation)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
