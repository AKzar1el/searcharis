"""Create a reviewable starter manifest from local static HTML build artifacts.

This convenience CLI never fetches URLs or guesses observed SEO metadata.
The declared base URL is supplied explicitly by the operator.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

from searcharis.cli_error_display import human_input_error
from searcharis.contract_ci import _is_http_url
from searcharis.site_ci import (
    _HTML_EXTENSIONS,
    _MAX_PAGES,
    _manifest_path,
    _read_limited,
    read_manifest,
    verify_manifest,
)


def _base_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError as error:
        raise ValueError("Invalid site base URL") from error
    if (
        not _is_http_url(value)
        or "?" in value
        or "#" in value
        or (parts.path and not parts.path.endswith("/"))
    ):
        raise ValueError(
            "Invalid site base URL: provide an absolute HTTP(S) URL ending in / "
            "without query, fragment or credentials"
        )
    # urljoin can collapse raw dot segments, while an empty index route retains
    # the original base. Encoded dots may also be normalized by user agents.
    if any(part in {".", ".."} for part in unquote(parts.path).split("/")):
        raise ValueError("Invalid site base URL: path must not contain dot segments")
    # urljoin removes empty path segments for subpages but not for the root
    # index route; preserve one unambiguous URL prefix for every page.
    if "//" in parts.path:
        raise ValueError("Invalid site base URL: path must not contain repeated slashes")
    return value if parts.path else value + "/"


def _route_for(path: str) -> str:
    filename = path.rsplit("/", 1)[-1]
    if filename.lower() in {"index.html", "index.htm"}:
        return path[:-len(filename)]
    return path


def _reviewed_route(value: object) -> str:
    """Require a local, unescaped path relative to the explicit public base."""
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value.isprintable()
        or value.startswith("/")
        or any(char in value for char in "\\:%?#")
        or (value and any(part in {"", ".", ".."} for part in value.rstrip("/").split("/")))
        or value.endswith("//")
    ):
        raise ValueError(f"Unsafe reviewed public route: {value!r}")
    return value


def read_route_map(path: Path) -> dict[str, str]:
    """Read a bounded, explicit local route map without collapsing duplicate keys."""
    def unique_keys(pairs: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate route-map JSON key: {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(_read_limited(path), object_pairs_hook=unique_keys)
    except RecursionError as error:
        raise ValueError("Route-map JSON exceeds supported nesting depth") from error
    if not isinstance(payload, dict) or set(payload) != {"version", "routes"}:
        raise ValueError("Route map requires exactly 'version' and 'routes'")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("Unsupported route-map version (expected integer 1)")
    routes = payload["routes"]
    if not isinstance(routes, dict) or not 1 <= len(routes) <= _MAX_PAGES:
        raise ValueError(f"Route-map 'routes' must contain 1 to {_MAX_PAGES} entries")
    return routes


def read_language_map(path: Path) -> dict[str, str]:
    """Read an explicit build-path to language mapping without duplicate keys."""
    def unique_keys(pairs: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate language-map JSON key: {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(_read_limited(path), object_pairs_hook=unique_keys)
    except RecursionError as error:
        raise ValueError("Language-map JSON exceeds supported nesting depth") from error
    if not isinstance(payload, dict) or set(payload) != {"version", "languages"}:
        raise ValueError("Language map requires exactly 'version' and 'languages'")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("Unsupported language-map version (expected integer 1)")
    languages = payload["languages"]
    if not isinstance(languages, dict) or not 1 <= len(languages) <= _MAX_PAGES:
        raise ValueError(f"Language-map 'languages' must contain 1 to {_MAX_PAGES} entries")
    return languages


def read_hreflang_map(path: Path) -> list[dict[str, str]]:
    """Read reviewed groups of built pages, never infer locale URLs from filenames."""
    def unique_keys(pairs: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate hreflang-map JSON key: {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(_read_limited(path), object_pairs_hook=unique_keys)
    except RecursionError as error:
        raise ValueError("Hreflang-map JSON exceeds supported nesting depth") from error
    if not isinstance(payload, dict) or set(payload) != {"version", "clusters"}:
        raise ValueError("Hreflang map requires exactly 'version' and 'clusters'")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("Unsupported hreflang-map version (expected integer 1)")
    clusters = payload["clusters"]
    if not isinstance(clusters, list) or not 1 <= len(clusters) <= _MAX_PAGES:
        raise ValueError(f"Hreflang-map clusters must contain 1 to {_MAX_PAGES} groups")
    return clusters


def read_selected_paths(path: Path) -> tuple[str, ...]:
    """Read a bounded list of explicitly reviewed build paths, never URLs."""
    content = _read_limited(path)
    if content.startswith("\ufeff"):
        content = content[1:]
    selected = tuple(
        line for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if not 1 <= len(selected) <= _MAX_PAGES:
        raise ValueError(f"Selection file requires 1 to {_MAX_PAGES} build paths")
    return selected


def generate_manifest(
    build_dir: Path, base_url: str, *,
    only_paths: tuple[str, ...] = (),
    expect_self_canonical: bool = False, expect_indexable: bool = False,
    expect_followable: bool = False,
    expect_description: bool = False, expect_image_alt: bool = False,
    expect_lang_attribute: bool = False,
    skip_opt_ins_for: tuple[str, ...] = (),
    expect_noindex_for: tuple[str, ...] = (),
    route_overrides: dict[str, str] | None = None,
    language_overrides: dict[str, str] | None = None,
    hreflang_clusters: list[dict[str, str]] | None = None,
) -> dict:
    """Inventory static HTML as candidate URL contracts, without reading HTML."""
    base = _base_url(base_url)
    root = build_dir.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Build root is not a directory")

    paths: list[str] = []
    for candidate in root.rglob("*"):
        if candidate.is_symlink() and (
            candidate.is_dir() or candidate.suffix.lower() in _HTML_EXTENSIONS
        ):
            raise ValueError(
                f"Symlinked build HTML or directory cannot be inventoried: "
                f"{candidate.relative_to(root).as_posix()}"
            )
        if not candidate.is_file() or candidate.suffix.lower() not in _HTML_EXTENSIONS:
            continue
        if not candidate.resolve().is_relative_to(root):
            raise ValueError(f"HTML artifact resolves outside build root: {candidate}")
        paths.append(_manifest_path(candidate.relative_to(root).as_posix()))
        if len(paths) > _MAX_PAGES:
            raise ValueError(f"Build contains more than {_MAX_PAGES} HTML pages")

    if not paths:
        raise ValueError("No HTML pages found in the supplied build directory")

    # Keep the full build inventory and path safety checks, but allow operators
    # to review only explicitly chosen pages during incremental CI adoption.
    if only_paths:
        available_paths = set(paths)
        selected: set[str] = set()
        for requested in only_paths:
            path = _manifest_path(requested)
            if path in selected:
                raise ValueError(f"duplicate selected path: {path}")
            if path not in available_paths:
                raise ValueError(f"unknown selected path: {path}")
            selected.add(path)
        paths = sorted(selected)

    if skip_opt_ins_for and not (
        expect_self_canonical or expect_indexable or expect_description
        or expect_followable or expect_image_alt or expect_lang_attribute
        or language_overrides is not None
        or hreflang_clusters is not None
    ):
        raise ValueError("Cannot skip optional assertions when none are requested")
    skipped: set[str] = set()
    available = set(paths)
    for requested in skip_opt_ins_for:
        path = _manifest_path(requested)
        if path in skipped:
            raise ValueError(f"duplicate skip opt-ins path: {path}")
        if path not in available:
            raise ValueError(f"unknown skip opt-ins path: {path}")
        skipped.add(path)

    noindex_paths: set[str] = set()
    for requested in expect_noindex_for:
        path = _manifest_path(requested)
        if path in noindex_paths:
            raise ValueError(f"duplicate expect-noindex path: {path}")
        if path not in available:
            raise ValueError(f"unknown expect-noindex path: {path}")
        noindex_paths.add(path)

    reviewed_routes: dict[str, str] = {}
    if route_overrides is not None:
        if not isinstance(route_overrides, dict) or not route_overrides:
            raise ValueError("Route overrides require a nonempty path-to-route mapping")
        if len(route_overrides) > _MAX_PAGES:
            raise ValueError(f"Route overrides exceed {_MAX_PAGES} pages")
        for requested, route in route_overrides.items():
            path = _manifest_path(requested)
            if path not in available:
                raise ValueError(f"unknown route-map build path: {path}")
            reviewed_routes[path] = _reviewed_route(route)

    reviewed_languages: dict[str, str] = {}
    if language_overrides is not None:
        if not isinstance(language_overrides, dict) or not language_overrides:
            raise ValueError("Language overrides require a nonempty path-to-language mapping")
        if len(language_overrides) > _MAX_PAGES:
            raise ValueError(f"Language overrides exceed {_MAX_PAGES} pages")
        for requested, language in language_overrides.items():
            path = _manifest_path(requested)
            if path not in available:
                raise ValueError(f"unknown language-map build path: {path}")
            if path in skipped:
                raise ValueError(f"language-map path cannot also be skipped: {path}")
            if (
                not isinstance(language, str)
                or len(language) > 63
                or not re.fullmatch(r"[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*", language)
            ):
                raise ValueError(f"Invalid language-map tag for build path: {path}")
            reviewed_languages[path] = language
        missing = sorted(available - skipped - reviewed_languages.keys())
        if missing:
            raise ValueError(f"language map missing reviewed build path: {missing[0]}")

    reviewed_clusters: dict[str, dict[str, str]] = {}
    if hreflang_clusters is not None:
        if (
            not isinstance(hreflang_clusters, list)
            or not 1 <= len(hreflang_clusters) <= _MAX_PAGES
        ):
            raise ValueError("Hreflang clusters require a nonempty bounded list")
        for cluster in hreflang_clusters:
            if not isinstance(cluster, dict) or not 2 <= len(cluster) <= 64:
                raise ValueError("Each hreflang cluster requires 2 to 64 language entries")
            languages: set[str] = set()
            cluster_paths: dict[str, str] = {}
            locale_paths: set[str] = set()
            for language, requested in cluster.items():
                if (
                    not isinstance(language, str)
                    or len(language) > 63
                    or not re.fullmatch(r"[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*", language)
                ):
                    raise ValueError(f"Invalid hreflang cluster language: {language!r}")
                normalized = language.lower()
                if normalized in languages:
                    raise ValueError(f"Duplicate hreflang language: {language!r}")
                languages.add(normalized)
                path = _manifest_path(requested)
                if path not in available:
                    raise ValueError(f"Unknown hreflang build path: {path}")
                if path in skipped:
                    raise ValueError(f"Hreflang path cannot also be skipped: {path}")
                if normalized != "x-default":
                    if path in locale_paths:
                        raise ValueError(f"Multiple hreflang languages share a page: {path}")
                    locale_paths.add(path)
                cluster_paths[language] = path
            if len(locale_paths) < 2:
                raise ValueError("Hreflang cluster needs at least two distinct locale pages")
            for path in set(cluster_paths.values()):
                if path in reviewed_clusters:
                    raise ValueError(f"Build path appears in multiple hreflang clusters: {path}")
                reviewed_clusters[path] = cluster_paths

    seen_urls: set[str] = set()
    public_urls: dict[str, str] = {}
    for path in sorted(paths):
        route = reviewed_routes.get(path, _route_for(path))
        url = urljoin(base, quote(route, safe="/-._~"))
        if url in seen_urls:
            raise ValueError(f"Multiple build HTML pages map to the same URL: {url}")
        seen_urls.add(url)
        public_urls[path] = url

    pages = []
    for path in sorted(paths):
        url = public_urls[path]
        contract = {"url": url, "title_present": True}
        if path not in skipped:
            if expect_self_canonical:
                contract["canonical"] = url
            if expect_indexable and path not in noindex_paths:
                contract["indexable"] = True
            if expect_followable:
                contract["followable"] = True
            if expect_description:
                contract["description_present"] = True
            if expect_image_alt:
                contract["img_missing_alt_max"] = 0
            if path in reviewed_languages:
                contract["html_lang"] = reviewed_languages[path]
            elif expect_lang_attribute:
                contract["html_lang_present"] = True
            if path in reviewed_clusters:
                contract["hreflang"] = {
                    language: public_urls[target_path]
                    for language, target_path in reviewed_clusters[path].items()
                }
        # An explicit negative assertion survives a skip of positive opt-ins.
        if path in noindex_paths:
            contract["indexable"] = False
        pages.append({
            "path": path,
            "contract": contract,
        })
    return {"version": 1, "pages": pages}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print a reviewable starter SEO manifest from local built HTML"
    )
    parser.add_argument("build_dir", type=Path, help="Local static build output")
    parser.add_argument(
        "site_base_url",
        help="Explicit public URL prefix (for example https://example.com/)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write UTF-8 JSON to a new file (never overwrite an existing file)",
    )
    parser.add_argument(
        "--only", metavar="BUILD_PATH", action="append", default=[],
        help="Generate assertions only for this exact built HTML path "
             "(repeatable; other pages remain unreviewed in incremental CI)",
    )
    parser.add_argument(
        "--only-file", type=Path, metavar="PATHS_TXT",
        help="Read UTF-8 newline-delimited exact build HTML paths for incremental "
             "review (blank lines and # comments ignored; combines with --only)",
    )
    parser.add_argument(
        "--extend-from", type=Path, metavar="REVIEWED_JSON",
        help="Add --only/--only-file selected pages to an existing reviewed manifest, preserving "
             "all earlier contracts; output is create-only",
    )
    parser.add_argument(
        "--expect-self-canonical",
        action="store_true",
        help="Opt in to requiring each page's canonical to equal its candidate public URL",
    )
    parser.add_argument(
        "--expect-indexable",
        action="store_true",
        help="Opt in to flagging noindex robots/googlebot meta in every built page",
    )
    parser.add_argument(
        "--expect-followable",
        action="store_true",
        help="Opt in to flagging nofollow/none robots/googlebot meta in every built page",
    )
    parser.add_argument(
        "--expect-description",
        action="store_true",
        help="Opt in to requiring non-empty head meta descriptions in every built page",
    )
    parser.add_argument(
        "--expect-image-alt",
        action="store_true",
        help="Opt in to requiring an alt attribute on every built HTML image (empty alt is valid)",
    )
    parser.add_argument(
        "--expect-lang-attribute",
        action="store_true",
        help="Opt in to requiring a non-blank root HTML lang attribute on every built page",
    )
    parser.add_argument(
        "--skip-opt-ins-for", metavar="BUILD_PATH", action="append", default=[],
        help="Keep this exact built HTML path in coverage, but omit optional assertions "
             "for it (repeatable; title is still required)",
    )
    parser.add_argument(
        "--expect-noindex-for", metavar="BUILD_PATH", action="append", default=[],
        help="Require HTML robots/googlebot noindex for this exact built HTML path "
             "(repeatable; can be combined with --skip-opt-ins-for)",
    )
    parser.add_argument(
        "--route-map", type=Path, metavar="JSON_FILE",
        help="Apply reviewed build-path to public-route overrides from a local JSON file",
    )
    parser.add_argument(
        "--lang-map", type=Path, metavar="JSON_FILE",
        help="Require exact reviewed HTML languages for every non-exempt built page",
    )
    parser.add_argument(
        "--hreflang-map", type=Path, metavar="JSON_FILE",
        help="Require reviewed reciprocal alternate URLs for explicitly grouped built pages",
    )
    parser.add_argument(
        "--check-built", action="store_true",
        help="Also verify generated SEO assertions against local HTML, exiting 1 on mismatch",
    )
    args = parser.parse_args(argv)
    created_output: tuple[Path, tuple[int, int] | None] | None = None
    try:
        selected_paths = (
            *args.only,
            *(read_selected_paths(args.only_file) if args.only_file else ()),
        )
        if args.extend_from is not None and not selected_paths:
            raise ValueError("--extend-from requires --only or --only-file selections")
        manifest = generate_manifest(
            args.build_dir, args.site_base_url,
            only_paths=selected_paths,
            expect_self_canonical=args.expect_self_canonical,
            expect_indexable=args.expect_indexable,
            expect_followable=args.expect_followable,
            expect_description=args.expect_description,
            expect_image_alt=args.expect_image_alt,
            expect_lang_attribute=args.expect_lang_attribute,
            skip_opt_ins_for=tuple(args.skip_opt_ins_for),
            expect_noindex_for=tuple(args.expect_noindex_for),
            route_overrides=read_route_map(args.route_map) if args.route_map else None,
            language_overrides=read_language_map(args.lang_map) if args.lang_map else None,
            hreflang_clusters=read_hreflang_map(args.hreflang_map) if args.hreflang_map else None,
        )
        if args.extend_from is not None:
            existing = read_manifest(args.extend_from)
            # Validate retained reviews as input even if a built page now fails
            # an assertion. A mismatched review is not permission to replace it.
            verify_manifest(existing, args.build_dir)
            manifest = {
                "version": 1,
                # Preserve the existing reviewed ordering and append only the
                # deterministically generated additions for a small review diff.
                "pages": [*existing["pages"], *manifest["pages"]],
            }
            # Catch duplicate paths/URLs and malformed combined input before
            # opening an output file, even without --check-built.
            verify_manifest(manifest, args.build_dir)
        # Validate before creating the output file so malformed HTML never
        # leaves a misleading manifest. Assertion mismatches remain reviewable.
        checked = (
            verify_manifest(
                manifest, args.build_dir, require_full_coverage=not bool(selected_paths)
            )
            if args.check_built else None
        )
        payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        if args.output is not None:
            if args.output.resolve().is_relative_to(args.build_dir.resolve()):
                raise ValueError("Starter manifest output must be outside the build directory")
            with args.output.open("x", encoding="utf-8", newline="\n") as output:
                # Retain the newly created file's identity before attempting any write.
                created_output = (args.output, None)
                info = os.fstat(output.fileno())
                created_output = (args.output, (info.st_dev, info.st_ino))
                output.write(payload)
    except (OSError, UnicodeError, ValueError) as error:
        cleanup_message = ""
        if created_output is not None:
            path, identity = created_output
            try:
                current = path.lstat()
                if (
                    identity is None or not identity[1]
                    or not current.st_ino
                    or (current.st_dev, current.st_ino) != identity
                ):
                    cleanup_message = "; partial manifest cleanup skipped: file identity changed or unavailable"
                else:
                    path.unlink()
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                cleanup_message = (
                    f"; partial manifest cleanup failed: {human_input_error(cleanup_error)}"
                )
        print(f"Invalid input: {human_input_error(error)}{cleanup_message}", file=sys.stderr)
        return 2
    if args.output is None:
        print(payload, end="")
    if checked is not None:
        summary = checked["summary"]
        print(
            f"{checked['status'].upper()} {summary['passed']}/{summary['checked']} "
            "generated SEO contracts passed against local HTML",
            file=sys.stderr,
        )
        # Bounded, escaped diagnostics are safe for untrusted build filenames.
        failed = [page for page in checked["pages"] if page["status"] == "fail"]
        for page in failed[:10]:
            label = human_input_error(ValueError(page["path"]), limit=100)
            codes = ", ".join(issue["code"] for issue in page["issues"][:8])
            print(f"  FAIL {label}: {codes}", file=sys.stderr)
        if len(failed) > 10:
            print(f"  ... and {len(failed) - 10} more failing pages", file=sys.stderr)
        return 0 if checked["status"] == "pass" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
