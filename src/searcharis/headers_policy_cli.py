"""Check approved Cloudflare Pages _headers byte parity in a static build.

This offline artifact gate does not interpret Cloudflare header-rule semantics,
Pages Functions responses, or live domain configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from searcharis.cli_error_display import human_input_error

# Bound local IO; this is a checker limit, not Cloudflare's documented file limit.
_MAX_HEADERS_BYTES = 8_500_000


def _read_bounded(path: Path) -> bytes:
    with path.open("rb") as handle:
        content = handle.read(_MAX_HEADERS_BYTES + 1)
    if len(content) > _MAX_HEADERS_BYTES:
        raise ValueError(f"Reviewed _headers artifact exceeds {_MAX_HEADERS_BYTES}-byte limit")
    return content


def verify_reviewed_headers_policy(reviewed_policy: Path, build_dir: Path) -> dict:
    """Hash the expected source and exact-case built _headers, with no URL fetch."""
    root = build_dir.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Static build root must be a directory")

    expected = reviewed_policy.resolve(strict=True)
    if not expected.is_file() or expected.is_relative_to(root):
        raise ValueError("Reviewed headers policy must be a file outside the static build")

    expected_digest = hashlib.sha256(_read_bounded(expected)).hexdigest()
    # On case-insensitive NTFS, Path(root / "_headers").exists() is not enough.
    built = next((item for item in root.iterdir() if item.name == "_headers"), None)
    if built is not None and (built.is_symlink() or not built.is_file()):
        raise ValueError("Built _headers must be a regular file, not a symlink")

    observed_digest = hashlib.sha256(_read_bounded(built)).hexdigest() if built else None
    if observed_digest is None:
        reason = "missing"
    elif observed_digest != expected_digest:
        reason = "changed"
    else:
        reason = None

    return {
        "status": "pass" if reason is None else "fail",
        "artifact": "_headers",
        "reason": reason,
        "expected_sha256": expected_digest,
        "observed_sha256": observed_digest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check approved Cloudflare Pages _headers bytes in a static build (offline)",
    )
    parser.add_argument("reviewed_policy", type=Path, help="Approved _headers file outside the build")
    parser.add_argument("build_dir", type=Path, help="Static build output containing root _headers")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Emit a machine-readable report")
    output.add_argument(
        "--json-file", type=Path, metavar="PATH",
        help="Write complete JSON to a new UTF-8 file outside the build; never overwrite",
    )
    args = parser.parse_args(argv)
    try:
        result = verify_reviewed_headers_policy(args.reviewed_policy, args.build_dir)
    except (OSError, ValueError) as error:
        if args.json:
            print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        else:
            print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
        return 2

    if args.json_file:
        target = args.json_file
        created_identity: tuple[int, int] | None = None
        created = False
        try:
            if target.resolve().is_relative_to(args.build_dir.resolve(strict=True)):
                raise ValueError("JSON report must be outside the static build")
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"JSON report already exists: {target}")
            if not target.parent.is_dir():
                raise ValueError(f"JSON report parent must be an existing directory: {target.parent}")
            with target.open("x", encoding="utf-8", newline="\n") as output_file:
                created = True
                info = os.fstat(output_file.fileno())
                created_identity = (info.st_dev, info.st_ino)
                output_file.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
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
            return 2
    elif args.json:
        print(json.dumps(result, sort_keys=True))
    elif result["status"] == "pass":
        print(f"PASS reviewed _headers SHA-256 {result['expected_sha256']}")
    else:
        print(
            f"FAIL reviewed _headers {result['reason']}: "
            f"expected SHA-256 {result['expected_sha256']}, "
            f"observed SHA-256 {result['observed_sha256'] or 'missing'}"
        )
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
