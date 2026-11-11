"""Create a reviewable starter manifest from local static HTML build artifacts.

This convenience CLI never fetches URLs or guesses observed SEO metadata.
The declared base URL is supplied explicitly by the operator.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

from searcharis.cli_error_display import human_input_error
from searcharis.contract_ci import _is_http_url
from searcharis.site_ci import _HTML_EXTENSIONS, _MAX_PAGES, _manifest_path


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
    return value if parts.path else value + "/"


def _route_for(path: str) -> str:
    filename = path.rsplit("/", 1)[-1]
    if filename.lower() in {"index.html", "index.htm"}:
        return path[:-len(filename)]
    return path


def generate_manifest(build_dir: Path, base_url: str) -> dict:
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

    seen_urls: set[str] = set()
    pages = []
    for path in sorted(paths):
        url = urljoin(base, quote(_route_for(path), safe="/-._~"))
        if url in seen_urls:
            raise ValueError(f"Multiple build HTML pages map to the same URL: {url}")
        seen_urls.add(url)
        pages.append({
            "path": path,
            "contract": {"url": url, "title_present": True},
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
    args = parser.parse_args(argv)
    try:
        manifest = generate_manifest(args.build_dir, args.site_base_url)
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
