"""Fail closed on unreviewed built robots.txt drift, without fetching any URL.

This deliberately compares exact artifact bytes instead of attempting to
approximate Googlebot's user-agent grouping and rule precedence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from searcharis.cli_error_display import human_input_error

_MAX_POLICY_BYTES = 512_000


def _read_bounded(path: Path) -> bytes:
    with path.open("rb") as source:
        data = source.read(_MAX_POLICY_BYTES + 1)
    if len(data) > _MAX_POLICY_BYTES:
        raise ValueError(f"robots.txt policy exceeds {_MAX_POLICY_BYTES}-byte limit: {path}")
    return data


def verify_reviewed_robots_policy(reviewed_policy: Path, build_dir: Path) -> dict:
    """Compare reviewed source policy to the exact build-root robots.txt artifact."""
    root = build_dir.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Static build root must be a directory: {build_dir}")

    expected = reviewed_policy.resolve(strict=True)
    if not expected.is_file() or expected.is_relative_to(root):
        raise ValueError("Reviewed robots policy must be a file outside the static build")
    expected_digest = hashlib.sha256(_read_bounded(expected)).hexdigest()

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
    return {
        "status": "pass" if matches else "fail",
        "artifact": "robots.txt",
        "reason": None if matches else ("missing" if observed_digest is None else "changed"),
        "expected_sha256": expected_digest,
        "observed_sha256": observed_digest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare a reviewed robots.txt policy with the local static build (no URL fetch)",
    )
    parser.add_argument("reviewed_policy", type=Path, help="Reviewed robots.txt outside the build")
    parser.add_argument("build_dir", type=Path, help="Local static build with root robots.txt")
    parser.add_argument("--json", action="store_true", help="Emit a structured report")
    args = parser.parse_args(argv)

    try:
        report = verify_reviewed_robots_policy(args.reviewed_policy, args.build_dir)
    except (OSError, ValueError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["status"] == "pass":
        print(f"PASS robots.txt matches reviewed SHA-256 {report['expected_sha256'][:12]}")
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
