"""Deterministic, offline verification of an explicit static-build page manifest.

Manifest URLs are assertions only. The checker reads local HTML artifacts and
never fetches a contract URL or invokes the deployed incident workflow.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from searcharis.contract_ci import verify_contract

_MAX_INPUT_BYTES = 4_000_000
_MAX_PAGES = 10_000
_MAX_DISCOVERED = 20_000


def _read_limited(path: Path) -> str:
    if path.stat().st_size > _MAX_INPUT_BYTES:
        raise ValueError(f"File exceeds {_MAX_INPUT_BYTES} bytes: {path}")
    return path.read_text(encoding="utf-8")


def _manifest_path(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Each page 'path' must be a non-empty relative POSIX path")
    # A manifest must mean the same thing on Windows and Linux CI runners.
    if (
        "\\" in value
        or ":" in value
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or PurePosixPath(value).suffix.lower() != ".html"
    ):
        raise ValueError(f"Unsafe or non-HTML manifest path: {value!r}")
    return value


def verify_manifest(
    manifest: object, build_dir: Path, *, require_full_coverage: bool = False
) -> dict:
    """Verify declared pages, report unlisted HTML; reject unsafe or missing inputs."""
    if not isinstance(manifest, dict) or set(manifest) != {"version", "pages"}:
        raise ValueError("Manifest requires exactly 'version' and 'pages'")
    if type(manifest["version"]) is not int or manifest["version"] != 1:
        raise ValueError("Unsupported manifest version (expected integer 1)")
    pages = manifest["pages"]
    if not isinstance(pages, list) or not 1 <= len(pages) <= _MAX_PAGES:
        raise ValueError(f"'pages' must contain between 1 and {_MAX_PAGES} entries")
    root = build_dir.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Build root is not a directory: {build_dir}")

    reports: list[dict] = []
    listed: set[str] = set()
    urls: set[str] = set()
    for entry in pages:
        if not isinstance(entry, dict) or set(entry) != {"path", "contract"}:
            raise ValueError("Each page requires exactly 'path' and 'contract'")
        path = _manifest_path(entry["path"])
        if path.casefold() in listed:
            raise ValueError(f"Duplicate manifest page path: {path}")
        listed.add(path.casefold())
        html_path = (root / path).resolve()
        if not html_path.is_relative_to(root) or not html_path.is_file():
            raise ValueError(f"Missing or unsafe built HTML artifact: {path}")
        report = verify_contract(entry["contract"], _read_limited(html_path))
        if report["checked_url"] in urls:
            raise ValueError(f"Duplicate contract URL: {report['checked_url']}")
        urls.add(report["checked_url"])
        reports.append({"path": path, **report})

    # Include every built HTML artifact in the coverage denominator, without
    # assuming that a partial adoption must block a release.
    discovered: set[str] = set()
    for candidate in root.rglob("*"):
        if not candidate.is_file() or candidate.suffix.lower() != ".html":
            continue
        if not candidate.resolve().is_relative_to(root):
            raise ValueError(f"HTML artifact resolves outside build root: {candidate}")
        discovered.add(candidate.relative_to(root).as_posix())
        if len(discovered) > _MAX_DISCOVERED:
            raise ValueError(f"Build contains more than {_MAX_DISCOVERED} HTML artifacts")
    unlisted = sorted(path for path in discovered if path.casefold() not in listed)
    reports.sort(key=lambda report: report["path"])
    failed = sum(report["status"] == "fail" for report in reports)
    passed = len(reports) - failed
    coverage_failed = require_full_coverage and bool(unlisted)

    return {
        "status": "fail" if failed or coverage_failed else "pass",
        "summary": {"checked": len(reports), "passed": passed, "failed": failed},
        "coverage": {
            "discovered": len(discovered),
            "checked": len(reports),
            "unlisted": unlisted,
            "require_full_coverage": require_full_coverage,
        },
        "pages": reports,
    }


def read_manifest(path: Path) -> object:
    """Read JSON manifest with the same bounded input size as HTML pages."""
    return json.loads(_read_limited(path))
