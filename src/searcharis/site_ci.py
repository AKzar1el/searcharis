"""Deterministic, offline verification of an explicit static-build page manifest.

Manifest URLs are assertions only. The checker reads local HTML artifacts and
never fetches a contract URL or invokes the deployed incident workflow.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from searcharis.contract_ci import _is_canonical_url, verify_contract

_MAX_INPUT_BYTES = 4_000_000
_MAX_PAGES = 10_000
_MAX_DISCOVERED = 20_000
_HTML_EXTENSIONS = frozenset({".html", ".htm"})
_ENCODED_OCTET = re.compile(r"%[0-9a-fA-F]{2}")


def _read_limited(path: Path) -> str:
    # Metadata can become stale while a CI producer is writing the artifact.
    # Bound the actual read, not a preceding stat, to avoid false passes/rejects.
    with path.open("rb") as stream:
        data = stream.read(_MAX_INPUT_BYTES + 1)
    if len(data) > _MAX_INPUT_BYTES:
        raise ValueError(f"File exceeds {_MAX_INPUT_BYTES} bytes: {path}")
    # Preserve read_text's universal newline normalization on Windows.
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _manifest_path(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Each page 'path' must be a non-empty relative POSIX path")
    # A manifest must mean the same thing on Windows and Linux CI runners.
    if (
        "\\" in value
        or ":" in value
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or PurePosixPath(value).suffix.lower() not in _HTML_EXTENSIONS
    ):
        raise ValueError(f"Unsafe or non-HTML manifest path: {value!r}")
    return value


def _normalize_percent_encoding(component: str) -> str:
    """Decode only unreserved ASCII; preserve reserved encodings as uppercase hex."""

    def replacement(match: re.Match[str]) -> str:
        character = chr(int(match.group()[1:], 16))
        if character.isascii() and (character.isalnum() or character in "-._~"):
            return character
        return match.group().upper()

    return _ENCODED_OCTET.sub(replacement, component)


def _remove_dot_segments(path: str) -> str:
    """Remove whole URI dot-segments, preserving empty and reserved path segments."""
    remaining = path
    output = ""
    # RFC 3986 section 5.2.4: operate on the path, never on the query.
    while remaining:
        if remaining.startswith("../"):
            remaining = remaining[3:]
        elif remaining.startswith("./") or remaining.startswith("/./"):
            remaining = remaining[2:]
        elif remaining == "/.":
            remaining = "/"
        elif remaining.startswith("/../"):
            remaining = remaining[3:]
            output = output.rsplit("/", 1)[0]
        elif remaining == "/..":
            remaining = "/"
            output = output.rsplit("/", 1)[0]
        elif remaining in (".", ".."):
            remaining = ""
        else:
            segment_start = 1 if remaining.startswith("/") else 0
            next_separator = remaining.find("/", segment_start)
            if next_separator == -1:
                output += remaining
                remaining = ""
            else:
                output += remaining[:next_separator]
                remaining = remaining[next_separator:]
    return output


def _page_url_identity(url: str) -> tuple[str, str, int | None, str, str]:
    """Compare HTTP(S) page identities without collapsing distinct routes or queries."""
    parts = urlsplit(url)
    # RFC 3986 6.2.2 and 6.2.3: default ports, empty roots and percent-
    # encoded unreserved octets and complete dot-segments do not identify
    # separate resources. Do not rewrite observed URL strings in reports.
    # Never decode reserved delimiters such as %2F into a literal slash.
    default_port = 80 if parts.scheme == "http" else 443
    port = None if parts.port in (None, default_port) else parts.port
    return (
        parts.scheme, parts.hostname or "", port,
        _remove_dot_segments(_normalize_percent_encoding(parts.path or "/")),
        _normalize_percent_encoding(parts.query),
    )


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
    urls: set[tuple[str, str, int | None, str, str]] = set()
    observed_links: dict[str, dict[str, list[str]]] = {}
    observed_canonicals: dict[str, list[str]] = {}
    for entry in pages:
        if not isinstance(entry, dict) or set(entry) != {"path", "contract"}:
            raise ValueError("Each page requires exactly 'path' and 'contract'")
        path = _manifest_path(entry["path"])
        if path in listed:
            raise ValueError(f"Duplicate manifest page path: {path}")
        listed.add(path)
        html_path = (root / path).resolve()
        if not html_path.is_relative_to(root) or not html_path.is_file():
            raise ValueError(f"Missing or unsafe built HTML artifact: {path}")
        report = verify_contract(
            entry["contract"],
            _read_limited(html_path),
            include_hreflang=check_hreflang_reciprocity or check_hreflang_clusters,
            include_canonical=check_canonical_consistency,
        )
        url_identity = _page_url_identity(report["checked_url"])
        if url_identity in urls:
            raise ValueError(f"Duplicate contract URL: {report['checked_url']}")
        urls.add(url_identity)
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
        # The manifest already rejects duplicate normalized page identities.
        # Resolve local canonical targets with that same identity rule rather
        # than treating a hostname/port spelling variant as an external URL.
        by_identity = {
            _page_url_identity(report["checked_url"]): report["checked_url"]
            for report in reports
        }
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
                if not _is_canonical_url(target):
                    report["issues"].append({
                        "code": "canonical_invalid_url",
                        "expected": "an absolute HTTP(S) canonical URL without fragments",
                        "actual": target,
                    })
                else:
                    target_identity = _page_url_identity(target)
                    if target_identity != _page_url_identity(source_url):
                        destination_url = by_identity.get(target_identity)
                        if destination_url is None:
                            unverified_targets.append({
                                "path": report["path"], "target": target,
                            })
                        else:
                            checked_targets += 1
                            actual = observed_canonicals[destination_url]
                            destination_is_self = (
                                len(actual) == 1
                                and _is_canonical_url(actual[0])
                                and _page_url_identity(actual[0]) == target_identity
                            )
                            if not destination_is_self:
                                report["issues"].append({
                                    "code": "canonical_target_not_self",
                                    "target": target,
                                    "expected": [destination_url],
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
        if require_full_coverage and candidate.is_symlink():
            if candidate.is_dir():
                # pathlib does not descend into directory symlinks by default.
                # Their HTML routes would escape strict coverage silently.
                raise ValueError(
                    "Cannot verify full coverage through symlinked build directory: "
                    f"{candidate.relative_to(root).as_posix()}"
                )
            if candidate.suffix.lower() in _HTML_EXTENSIONS:
                # A linked HTML route may not survive artifact publishing; a
                # dangling link is not reported by is_file() at all.
                raise ValueError(
                    "Cannot verify full coverage through symlinked HTML artifact: "
                    f"{candidate.relative_to(root).as_posix()}"
                )
        if not candidate.is_file() or candidate.suffix.lower() not in _HTML_EXTENSIONS:
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

    try:
        return json.loads(_read_limited(path), object_pairs_hook=unique_keys)
    except RecursionError as error:
        # The JSON decoder can exhaust Python's recursion limit on a small
        # malformed manifest. Treat that as invalid CI input, not a crash.
        raise ValueError("Manifest JSON exceeds supported nesting depth") from error
