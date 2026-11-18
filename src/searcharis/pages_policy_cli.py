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
        elif item.get("reason") == "sitewide_noindex":
            message = f"Reviewed {artifact} declares broad noindex: {item['indexability_issue']}"
        else:
            message = f"Compiled {artifact} changed from the reviewed reference"
            if "drift_location" in item:
                message += f"; {_drift_location_note(item)}"
        annotations.append(f"::error title=SEO Pages {artifact}::{message}")
    return annotations


def _drift_location_note(item: dict) -> str:
    location = item["drift_location"]
    return (
        f"first differing byte at line {location['line']}, "
        f"column {location['byte_column']} (offset {location['byte_offset']})"
    )


def _format_human_check(item: dict) -> str:
    if item["status"] == "pass":
        return f"PASS {item['artifact']} reviewed SHA-256 {item['expected_sha256']}"
    if item["reason"] == "limit_exceeded":
        return f"FAIL {item['artifact']} Pages limit: {item['limit_issue']}"
    if item["reason"] == "sitewide_noindex":
        return f"FAIL {item['artifact']} sitewide noindex: {item['indexability_issue']}"
    message = (
        f"FAIL {item['artifact']} {item['reason']}: "
        f"expected SHA-256 {item['expected_sha256']}, "
        f"observed SHA-256 {item['observed_sha256'] or 'missing'}"
    )
    if "drift_location" in item:
        message += f"; {_drift_location_note(item)}"
    return message


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
        elif item["reason"] == "sitewide_noindex":
            lines.append(f"- **{artifact}: FAIL** - {item['indexability_issue']}")
        else:
            description = (
                f"- **{artifact}: FAIL** - {item['reason']}; "
                f"reviewed SHA-256 `{item['expected_sha256'][:12]}`, "
                f"built SHA-256 `{(item['observed_sha256'] or 'missing')[:12]}`"
            )
            if "drift_location" in item:
                description += f"; {_drift_location_note(item)}"
            lines.append(description)
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


def _read_reviewed_policy_lines(reviewed: Path, artifact: str, digest: str) -> list[str]:
    """Read bounded, unchanged reviewed text without interpreting deployed Pages rules."""
    # The individual byte-parity verifiers already enforce this bounded read.
    with reviewed.open("rb") as source:
        content = source.read(8_500_001)
    if len(content) > 8_500_000:
        raise ValueError(f"Reviewed {artifact} exceeds the local input byte limit")
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError(f"Reviewed {artifact} changed while checking optional policy guards")
    try:
        return content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"Reviewed {artifact} is not UTF-8 text") from error


def _sitewide_noindex_issue(
    reviewed: Path, digest: str, production_host: str | None = None,
) -> str | None:
    """Flag exact broad static rules, not effective hosted header behavior."""
    broad_rule = False
    scope = "/*"
    for line_number, line in enumerate(
        _read_reviewed_policy_lines(reviewed, "_headers", digest), start=1,
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[0].isspace():
            # An absolute Pages rule may target one hostname, including a
            # production domain distinct from intentionally noindexed previews.
            broad_rule = stripped == "/*" or (
                production_host is not None
                and stripped.lower() == f"https://{production_host}/*"
            )
            if broad_rule:
                scope = "/*" if stripped == "/*" else f"https://{production_host}/*"
            continue
        if not broad_rule or stripped.startswith("!"):
            continue
        name, separator, raw_directives = stripped.partition(":")
        if not separator or name.casefold() != "x-robots-tag":
            continue
        crawler = "all"
        for directive in raw_directives.casefold().split(","):
            directive = directive.strip()
            if ":" in directive:
                crawler, _, directive = directive.partition(":")
                directive = directive.strip()
            if crawler in {"all", "googlebot"} and directive in {"noindex", "none"}:
                return (
                    f"line {line_number} has an X-Robots-Tag noindex/none "
                    f"declaration under {scope} (all matching static paths)"
                )
    return None


def _validated_production_host(value: str) -> str:
    """Accept only an explicit ASCII DNS name, not a URL or path pattern."""
    label = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    if len(value) > 253 or not re.fullmatch(rf"{label}(?:\.{label})+", value):
        raise ValueError("--production-host must be a DNS hostname (not URL, port or wildcard)")
    return value.lower()


def _basic_pages_limit_issue(reviewed: Path, artifact: str, digest: str) -> str | None:
    """Check published basic text limits, not complete Pages syntax or routing."""
    lines = _read_reviewed_policy_lines(reviewed, artifact, digest)

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


def _locate_drift(
    reviewed: Path, built: Path, artifact: str,
    expected_sha256: str, observed_sha256: str,
) -> dict[str, int]:
    """Locate a byte difference without printing policy contents or fetching URLs."""
    if built.is_symlink() or not built.is_file():
        raise ValueError(f"Built {artifact} changed while locating drift")

    def read_verified(path: Path, digest: str) -> bytes:
        with path.open("rb") as source:
            data = source.read(8_500_001)
        if len(data) > 8_500_000 or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"{artifact} changed while locating drift")
        return data

    expected = read_verified(reviewed, expected_sha256)
    observed = read_verified(built, observed_sha256)
    shared = min(len(expected), len(observed))
    offset = next((i for i in range(shared) if expected[i] != observed[i]), shared)
    if offset == shared and len(expected) == len(observed):
        raise ValueError(f"{artifact} changed while locating drift")
    prefix = expected[:offset]
    return {
        "line": prefix.count(b"\n") + 1,
        "byte_column": offset - prefix.rfind(b"\n"),
        "byte_offset": offset,
    }


def verify_reviewed_pages_policies(
    reviewed_redirects: Path, reviewed_headers: Path, build_dir: Path,
    *, check_policy_limits: bool = False, locate_drift: bool = False,
    check_sitewide_noindex: bool = False, production_host: str | None = None,
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
            if locate_drift and result["reason"] == "changed":
                result["drift_location"] = _locate_drift(
                    reviewed, build_dir.resolve(strict=True) / artifact, artifact,
                    result["expected_sha256"], result["observed_sha256"],
                )
            if check_policy_limits and result["status"] == "pass":
                issue = _basic_pages_limit_issue(
                    reviewed, artifact, result["expected_sha256"],
                )
                if issue:
                    result.update(
                        status="fail", reason="limit_exceeded", limit_issue=issue,
                    )
            if (
                check_sitewide_noindex and artifact == "_headers"
                and result["status"] == "pass"
            ):
                issue = _sitewide_noindex_issue(
                    reviewed, result["expected_sha256"], production_host,
                )
                if issue:
                    result.update(
                        status="fail", reason="sitewide_noindex",
                        indexability_issue=issue,
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
    parser.add_argument(
        "--check-sitewide-noindex", action="store_true",
        help="Block explicit /* X-Robots-Tag noindex/none in reviewed static _headers",
    )
    parser.add_argument(
        "--production-host", metavar="HOST",
        help="With --check-sitewide-noindex, also inspect exact https://HOST/* rules",
    )
    parser.add_argument(
        "--locate-drift", action="store_true",
        help="Locate first changed byte/line in drifted artifacts without printing contents",
    )
    args = parser.parse_args(argv)
    if args.production_host is not None:
        if not args.check_sitewide_noindex:
            parser.error("--production-host requires --check-sitewide-noindex")
        try:
            args.production_host = _validated_production_host(args.production_host)
        except ValueError as error:
            parser.error(str(error))
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
        check_policy_limits=args.check_policy_limits, locate_drift=args.locate_drift,
        check_sitewide_noindex=args.check_sitewide_noindex,
        production_host=args.production_host,
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
