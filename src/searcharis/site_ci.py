"""Deterministic, offline verification of an explicit static-build page manifest.

Manifest URLs are assertions only. The checker reads local HTML artifacts and
never fetches a contract URL or invokes the deployed incident workflow.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

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
    manifest: object,
    build_dir: Path,
    *,
    require_full_coverage: bool = False,
    check_hreflang_reciprocity: bool = False,
    check_hreflang_clusters: bool = False,
    check_canonical_consistency: bool = False,
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
    observed_links: dict[str, dict[str, list[str]]] = {}
    observed_canonicals: dict[str, list[str]] = {}
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
        report = verify_contract(
            entry["contract"],
            _read_limited(html_path),
            include_hreflang=check_hreflang_reciprocity or check_hreflang_clusters,
            include_canonical=check_canonical_consistency,
        )
        if report["checked_url"] in urls:
            raise ValueError(f"Duplicate contract URL: {report['checked_url']}")
        urls.add(report["checked_url"])
        if check_hreflang_reciprocity or check_hreflang_clusters:
            observed_links[report["checked_url"]] = report.pop("observed_hreflang")
        if check_canonical_consistency:
            observed_canonicals[report["checked_url"]] = report.pop("observed_canonical")
        # Bind this route's findings to the assertions that produced them.
        # Key ordering and JSON whitespace do not change the fingerprint.
        contract_json = json.dumps(
            entry["contract"], sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
        reports.append({
            "path": path,
            "contract_sha256": hashlib.sha256(contract_json.encode("utf-8")).hexdigest(),
            **report,
        })

    hreflang_summary = None
    if check_hreflang_reciprocity:
        by_url = {report["checked_url"]: report for report in reports}
        checked_pairs = 0
        unverified_targets: list[dict[str, str]] = []
        for report in reports:
            source_url = report["checked_url"]
            links = observed_links[source_url]
            self_languages = sorted(
                language for language, targets in links.items()
                if language != "x-default" and source_url in targets
            )
            if any(language != "x-default" for language in links) and not self_languages:
                report["issues"].append({
                    "code": "hreflang_self_missing",
                    "expected": "a language-tagged alternate pointing to this page",
                    "actual": links,
                })

            # A pair is checked once even when several locale labels target it.
            targets = sorted({
                target for values in links.values() for target in values
                if target != source_url
            })
            for target in targets:
                if target not in by_url:
                    unverified_targets.append({
                        "path": report["path"], "target": target,
                    })
                    continue
                if not self_languages:
                    continue  # The missing self-label is already reported above.
                checked_pairs += 1
                for language in self_languages:
                    actual = observed_links[target].get(language, [])
                    if actual != [source_url]:
                        report["issues"].append({
                            "code": f"hreflang_return:{language}",
                            "target": target,
                            "expected": [source_url],
                            "actual": actual,
                        })
            if report["issues"]:
                report["status"] = "fail"
        hreflang_summary = {
            "checked_pairs": checked_pairs,
            "unverified_targets": sorted(
                unverified_targets, key=lambda item: (item["path"], item["target"])
            ),
        }

    cluster_summary = None
    if check_hreflang_clusters:
        by_url = {report["checked_url"]: report for report in reports}
        checked_pairs = 0
        unverified_targets: list[dict[str, str]] = []
        for report in reports:
            source_url = report["checked_url"]
            links = observed_links[source_url]
            for language, values in sorted(links.items()):
                if len(values) != 1:
                    report["issues"].append({
                        "code": f"hreflang_cluster_duplicate:{language}",
                        "expected": "exactly one alternate URL for this locale",
                        "actual": values,
                    })
                for target in values:
                    if target not in by_url:
                        unverified_targets.append({
                            "path": report["path"], "language": language, "target": target,
                        })

            # Compare only locally supplied destinations. External language
            # variants are inconclusive, never inferred to be missing or valid.
            peers = sorted({
                target for values in links.values() for target in values
                if target != source_url and target in by_url
            })
            for target in peers:
                checked_pairs += 1
                peer_links = observed_links[target]
                for language in sorted(set(links) | set(peer_links)):
                    expected = links.get(language, [])
                    actual = peer_links.get(language, [])
                    if any(value not in by_url for value in expected + actual):
                        continue
                    if actual != expected:
                        report["issues"].append({
                            "code": f"hreflang_cluster_mismatch:{language}",
                            "target": target,
                            "expected": expected,
                            "actual": actual,
                        })
            if report["issues"]:
                report["status"] = "fail"
        cluster_summary = {
            "checked_pairs": checked_pairs,
            "unverified_targets": sorted(
                unverified_targets,
                key=lambda item: (item["path"], item["language"], item["target"]),
            ),
        }

    canonical_summary = None
    if check_canonical_consistency:
        by_url = {report["checked_url"]: report for report in reports}
        checked_targets = 0
        unverified_targets: list[dict[str, str]] = []
        for report in reports:
            source_url = report["checked_url"]
            observed = observed_canonicals[source_url]
            if len(observed) != 1:
                report["issues"].append({
                    "code": "canonical_count",
                    "expected": "exactly one canonical link",
                    "actual": observed,
                })
            else:
                target = observed[0]
                try:
                    parsed = urlsplit(target)
                    valid = (
                        parsed.scheme in {"http", "https"}
                        and bool(parsed.hostname)
                        and parsed.username is None
                        and parsed.password is None
                        and (parsed.port is None or 0 < parsed.port <= 65535)
                        and not any(char.isspace() or ord(char) < 32 for char in target)
                    )
                except ValueError:
                    valid = False
                if not valid:
                    report["issues"].append({
                        "code": "canonical_invalid_url",
                        "expected": "an absolute HTTP(S) canonical URL",
                        "actual": target,
                    })
                elif target != source_url:
                    if target not in by_url:
                        unverified_targets.append({
                            "path": report["path"], "target": target,
                        })
                    else:
                        checked_targets += 1
                        actual = observed_canonicals[target]
                        if actual != [target]:
                            report["issues"].append({
                                "code": "canonical_target_not_self",
                                "target": target,
                                "expected": [target],
                                "actual": actual,
                            })
            if report["issues"]:
                report["status"] = "fail"
        canonical_summary = {
            "internal_targets_checked": checked_targets,
            "unverified_targets": sorted(
                unverified_targets, key=lambda item: (item["path"], item["target"])
            ),
        }

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
    # Case-insensitive filesystems can open a wrong-cased manifest path that
    # would 404 on a case-sensitive CI runner or deployment target.
    mismatched = sorted(report["path"] for report in reports if report["path"] not in discovered)
    if mismatched:
        raise ValueError(f"Manifest paths must match discovered HTML artifact casing: {mismatched}")
    # Coverage is about distinct built files, not casefold-equivalent names.
    # Case-sensitive builds may contain both en/index.html and en/INDEX.HTML;
    # only the exact manifest entry is checked, even on a Windows CI runner.
    unlisted = sorted(discovered - {report["path"] for report in reports})
    reports.sort(key=lambda report: report["path"])
    failed = sum(report["status"] == "fail" for report in reports)
    passed = len(reports) - failed
    coverage_failed = require_full_coverage and bool(unlisted)

    result = {
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
    if hreflang_summary is not None:
        result["hreflang_reciprocity"] = hreflang_summary
    if cluster_summary is not None:
        result["hreflang_clusters"] = cluster_summary
    if canonical_summary is not None:
        result["canonical_consistency"] = canonical_summary
    return result


def read_manifest(path: Path) -> object:
    """Read JSON manifest with the same bounded input size as HTML pages."""
    def unique_keys(pairs: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate manifest JSON key: {key!r}")
            result[key] = value
        return result

    return json.loads(_read_limited(path), object_pairs_hook=unique_keys)
