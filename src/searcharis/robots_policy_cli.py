"""Fail closed on unreviewed built robots.txt drift, without fetching any URL.

This deliberately compares exact artifact bytes instead of attempting to
approximate Googlebot's user-agent grouping and rule precedence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from searcharis.cli_error_display import human_input_error
from searcharis.contract_ci import _is_canonical_url

_MAX_POLICY_BYTES = 512_000
_MAX_REQUIRED_SITEMAPS = 16


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


def verify_reviewed_robots_policy(
    reviewed_policy: Path, build_dir: Path,
    *, required_sitemap_url: str | Sequence[str] | None = None,
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
    reason = (
        None if matches and sitemap_declared else
        "missing" if observed_digest is None else
        "changed" if not matches else "sitemap_declaration_missing"
    )
    result = {
        "status": "pass" if matches and sitemap_declared else "fail",
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
    parser.add_argument("--json", action="store_true", help="Emit a structured report")
    parser.add_argument(
        "--require-robots-sitemap", action="append", metavar="URL",
        help="Require a reviewed Sitemap URL (repeatable, up to 16; no URL fetch)",
    )
    args = parser.parse_args(argv)

    try:
        report = verify_reviewed_robots_policy(
            args.reviewed_policy, args.build_dir,
            required_sitemap_url=args.require_robots_sitemap,
        )
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
    elif report["reason"] == "sitemap_declaration_missing":
        missing = report.get("missing_sitemap_urls") or [report["required_sitemap_url"]]
        samples = ", ".join(
            human_input_error(ValueError(url), limit=96) for url in missing[:2]
        )
        rest = f" (+{len(missing) - 2} more)" if len(missing) > 2 else ""
        print(f"FAIL robots.txt missing reviewed Sitemap declaration(s): {samples}{rest}")
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
