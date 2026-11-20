"""Reject unreviewed changes to previously approved offline SEO assertions.

This append-only approval guard compares two local manifest files against the
same compiled build. It never fetches their declared public URLs. A successful
approval check is not an SEO assertion pass; run site_cli separately.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from html import escape
from pathlib import Path

from searcharis.cli_error_display import human_input_error
from searcharis.site_ci import read_manifest, verify_manifest


def _same_review(old: dict, new: dict) -> bool:
    """Compare JSON types exactly (Python considers True == 1)."""
    return json.dumps(old, sort_keys=True, allow_nan=False) == json.dumps(
        new, sort_keys=True, allow_nan=False
    )


def _approval_change(approved: list[dict], candidate: list[dict]) -> str | None:
    """Permit only appending new pages after the intact approved prefix."""
    for index, old in enumerate(approved):
        if index >= len(candidate):
            return f"approved page removed at position {index + 1}: {human_input_error(old['path'])}"
        current = candidate[index]
        if old["path"] != current["path"]:
            return (
                f"approved page removed, reordered or displaced at position "
                f"{index + 1}: {human_input_error(old['path'])}"
            )
        if not _same_review(old["contract"], current["contract"]):
            return (
                "approved SEO assertions changed at position "
                f"{index + 1}: {human_input_error(old['path'])}"
            )
    return None


def _github_approval_error(message: str, *, kind: str) -> str:
    """Bound and escape local manifest diagnostics before emitting workflow commands."""
    safe = (
        str(message).replace("%", "%25").replace("\r", "%0D")
        .replace("\n", "%0A").replace("::", ": :")
    )
    return f"::error title=SEO approval {kind}::{human_input_error(ValueError(safe), limit=240)}"


def _approval_markdown(report: dict) -> str:
    """Render a bounded reviewer summary without treating additions as approved."""
    def label(value: object) -> str:
        # repr makes control characters visible; HTML escaping protects Markdown.
        shown = repr(value)
        if len(shown) > 160:
            shown = shown[:157] + "..."
        return f"<code>{escape(shown, quote=True)}</code>"

    lines = [
        "# Searcharis SEO manifest approval",
        "",
        f"**Result: {report['status'].upper()}** (offline approval integrity only)",
        "",
    ]
    if report["status"] == "fail":
        lines.extend([
            "## Blocking changes to approved assertions",
            "",
            f"- {label(report['reason'])}",
            "",
            "Restore the previously approved assertions or obtain a separate "
            "explicit review of the changed contract.",
        ])
    else:
        count = report["new_pages"]
        lines.extend([
            f"- **Approved prefix:** {report['approved_pages']} previously approved pages unchanged",
            f"- **Appended pages:** {count} (not automatically approved)",
            "",
        ])
        if count:
            lines.extend([
                "## UNREVIEWED new pages (manual review required)",
                "",
                "The candidate added these exact built HTML paths; check each "
                "new contract in the pull-request diff:",
            ])
            lines.extend(f"- {label(path)}" for path in report["unreviewed_paths"][:10])
            if count > 10:
                lines.append(
                    f"- ... and {count - 10} more unreviewed paths; "
                    "use --json for the full list"
                )
            lines.append("Passing this gate does not approve any appended page.")
        else:
            lines.append("No newly appended pages require manual review.")
    lines.extend([
        "",
        "_This checks supplied local manifests, not live SEO, indexing, "
        "customer adoption or revenue. Run the independent site verifier._",
    ])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Require an extended reviewed SEO manifest to retain every approved assertion"
    )
    parser.add_argument("approved_json", type=Path, help="Previously approved manifest")
    parser.add_argument("candidate_json", type=Path, help="Proposed expanded manifest")
    parser.add_argument("build_dir", type=Path, help="Local built HTML for input validation only")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Print machine-readable review result")
    output.add_argument(
        "--json-file", type=Path, metavar="PATH",
        help="Write complete review JSON to a new UTF-8 file outside the build; never overwrite",
    )
    output.add_argument(
        "--markdown-file", type=Path, metavar="PATH",
        help="Write a bounded, escaped manual-review summary to a new UTF-8 file",
    )
    parser.add_argument(
        "--github-annotations", action="store_true",
        help="Emit a bounded GitHub Actions error annotation on failure (not with --json stdout)",
    )
    args = parser.parse_args(argv)
    if args.github_annotations and args.json:
        parser.error("--github-annotations cannot be combined with --json stdout")

    try:
        approved = read_manifest(args.approved_json)
        candidate = read_manifest(args.candidate_json)
        # Both must be genuine valid manifests for this build. An SEO policy
        # mismatch is not an input error and is checked separately by site_cli.
        verify_manifest(approved, args.build_dir)
        verify_manifest(candidate, args.build_dir)
        change = _approval_change(approved["pages"], candidate["pages"])
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
            if args.github_annotations:
                print(_github_approval_error(str(error), kind="input"))
        return 2

    new_count = len(candidate["pages"]) - len(approved["pages"])
    report = (
        {"status": "fail", "reason": change}
        if change else {
            "status": "pass",
            "approved_pages": len(approved["pages"]),
            "new_pages": new_count,
            "review_required": bool(new_count),
            "unreviewed_paths": [
                entry["path"] for entry in candidate["pages"][len(approved["pages"]):]
            ],
        }
    )
    if args.json_file or args.markdown_file:
        is_markdown = bool(args.markdown_file)
        target = args.markdown_file if is_markdown else args.json_file
        kind = "Markdown" if is_markdown else "JSON"
        payload = (
            _approval_markdown(report) if is_markdown
            else json.dumps(report, indent=2, sort_keys=True) + "\n"
        )
        created_identity: tuple[int, int] | None = None
        created = False
        try:
            if target.resolve().is_relative_to(args.build_dir.resolve(strict=True)):
                raise ValueError(f"{kind} report must be outside the static build")
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"{kind} report already exists: {target}")
            if not target.parent.is_dir():
                raise ValueError(f"{kind} report parent must be an existing directory: {target.parent}")
            # Exclusive creation protects against another CI process winning the path.
            with target.open("x", encoding="utf-8", newline="\n") as output_file:
                created = True
                info = os.fstat(output_file.fileno())
                created_identity = (info.st_dev, info.st_ino)
                output_file.write(payload)
        except (OSError, UnicodeError, ValueError) as error:
            cleanup = ""
            if created:
                try:
                    current = target.lstat()
                    if (
                        created_identity is None or not created_identity[1]
                        or not current.st_ino
                        or (current.st_dev, current.st_ino) != created_identity
                    ):
                        cleanup = "; partial report cleanup skipped: identity changed or unavailable"
                    else:
                        target.unlink()
                except FileNotFoundError:
                    pass
                except OSError as cleanup_error:
                    cleanup = f"; partial report cleanup failed: {human_input_error(cleanup_error)}"
            print(f"Invalid input: {human_input_error(error)}{cleanup}", file=sys.stderr)
            if args.github_annotations:
                print(_github_approval_error(str(error) + cleanup, kind="input"))
            return 2
        if change and args.github_annotations:
            print(_github_approval_error(change, kind="integrity"))
        return 1 if change else 0
    if args.json:
        print(json.dumps(report, sort_keys=True))
        return 1 if change else 0
    if change:
        print(f"FAIL manifest approval integrity: {change}")
        if args.github_annotations:
            print(_github_approval_error(change, kind="integrity"))
        return 1
    print(
        "PASS manifest approval integrity: "
        f"{len(approved['pages'])} previously approved pages unchanged; "
        f"{new_count} new pages appended"
    )
    if new_count:
        # A successful integrity check never grants approval to appended pages.
        # Keep the reviewer queue bounded and safe for untrusted CI logs.
        print("UNREVIEWED new pages (manual review required):")
        for entry in candidate["pages"][len(approved["pages"]):][:10]:
            print(f"  - {human_input_error(entry['path'])}")
        if new_count > 10:
            print(f"  ... and {new_count - 10} more unreviewed pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
