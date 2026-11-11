"""Create a reviewable starter manifest from local static HTML build artifacts.

This convenience CLI never fetches URLs or guesses observed SEO metadata.
The declared base URL is supplied explicitly by the operator.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

from searcharis.cli_error_display import human_input_error
from searcharis.contract_ci import _is_http_url
from searcharis.site_ci import _HTML_EXTENSIONS, _MAX_PAGES, _manifest_path, _read_limited


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


def generate_manifest(
    build_dir: Path, base_url: str, *,
    expect_self_canonical: bool = False, expect_indexable: bool = False,
    expect_description: bool = False, skip_opt_ins_for: tuple[str, ...] = (),
    expect_noindex_for: tuple[str, ...] = (),
    route_overrides: dict[str, str] | None = None,
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

    if skip_opt_ins_for and not (
        expect_self_canonical or expect_indexable or expect_description
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

    seen_urls: set[str] = set()
    pages = []
    for path in sorted(paths):
        route = reviewed_routes.get(path, _route_for(path))
        url = urljoin(base, quote(route, safe="/-._~"))
        if url in seen_urls:
            raise ValueError(f"Multiple build HTML pages map to the same URL: {url}")
        seen_urls.add(url)
        contract = {"url": url, "title_present": True}
        if path not in skipped:
            if expect_self_canonical:
                contract["canonical"] = url
            if expect_indexable and path not in noindex_paths:
                contract["indexable"] = True
            if expect_description:
                contract["description_present"] = True
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
        "--expect-description",
        action="store_true",
        help="Opt in to requiring non-empty head meta descriptions in every built page",
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
    args = parser.parse_args(argv)
    try:
        manifest = generate_manifest(
            args.build_dir, args.site_base_url,
            expect_self_canonical=args.expect_self_canonical,
            expect_indexable=args.expect_indexable,
            expect_description=args.expect_description,
            skip_opt_ins_for=tuple(args.skip_opt_ins_for),
            expect_noindex_for=tuple(args.expect_noindex_for),
            route_overrides=read_route_map(args.route_map) if args.route_map else None,
        )
        payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        if args.output is not None:
            with args.output.open("x", encoding="utf-8", newline="\n") as output:
                output.write(payload)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Invalid input: {human_input_error(error)}", file=sys.stderr)
        return 2
    if args.output is None:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
