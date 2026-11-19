"""Reject unreviewed changes to previously approved offline SEO assertions.

This append-only approval guard compares two local manifest files against the
same compiled build. It never fetches their declared public URLs. A successful
approval check is not an SEO assertion pass; run site_cli separately.
"""

from __future__ import annotations

import argparse
import json
import sys
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Require an extended reviewed SEO manifest to retain every approved assertion"
    )
    parser.add_argument("approved_json", type=Path, help="Previously approved manifest")
    parser.add_argument("candidate_json", type=Path, help="Proposed expanded manifest")
    parser.add_argument("build_dir", type=Path, help="Local built HTML for input validation only")
    parser.add_argument("--json", action="store_true", help="Print machine-readable review result")
    args = parser.parse_args(argv)

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
        return 2

    if change:
        if args.json:
            print(json.dumps({"status": "fail", "reason": change}, sort_keys=True))
        else:
            print(f"FAIL manifest approval integrity: {change}")
        return 1
    new_count = len(candidate["pages"]) - len(approved["pages"])
    if args.json:
        print(json.dumps({
            "status": "pass",
            "approved_pages": len(approved["pages"]),
            "new_pages": new_count,
            "review_required": bool(new_count),
            "unreviewed_paths": [
                entry["path"] for entry in candidate["pages"][len(approved["pages"]):]
            ],
        }, sort_keys=True))
        return 0
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
