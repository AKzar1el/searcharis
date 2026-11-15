"""Check explicit SEO sitemap intent using only a local XML file and reviewed manifest.

No URL from either input is requested. Unasserted pages and unrelated sitemap
entries are nonblocking by default: the manifest is not a crawl map.
"""

from __future__ import annotations

import gzip
import re
import zlib
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

from searcharis.contract_ci import _is_canonical_url
from searcharis.site_ci import _MAX_PAGES, _manifest_path, _page_url_identity

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_MAX_SITEMAP_BYTES = 4_000_000
_MAX_SITEMAP_URLS = 50_000
_MAX_SITEMAP_FILES = 16
_MAX_TOTAL_SITEMAP_BYTES = 16_000_000
_MAX_TOTAL_SITEMAP_URLS = 100_000


def _local_index_shards(index_path: Path) -> tuple[tuple[Path, ...], tuple]:
    """Read only shard basenames adjacent to an explicitly supplied local index.

    Public XML loc values are identifiers, never filesystem paths or fetch URLs.
    Restricting index discovery to same-directory, literal XML filenames avoids
    guessing URL-to-disk mappings or following unreviewed locations.
    """
    if not isinstance(index_path, Path):
        raise ValueError("Sitemap index must be a local file path")
    if index_path.is_symlink() or not index_path.is_file():
        raise ValueError("Sitemap index must be a regular local file")
    parent = index_path.parent.resolve(strict=True)
    # NTFS may open a differently cased path successfully, but Linux build
    # runners and static hosts require the actual index filename's casing.
    if index_path.name not in {entry.name for entry in parent.iterdir()}:
        raise ValueError("Sitemap index path must match the exact-case local filename")
    with index_path.open("rb") as stream:
        data = stream.read(_MAX_SITEMAP_BYTES + 1)
    if len(data) > _MAX_SITEMAP_BYTES:
        raise ValueError(f"Sitemap index exceeds {_MAX_SITEMAP_BYTES} bytes")
    decoded = data.decode("utf-8-sig")
    if "<!DOCTYPE" in decoded.upper() or "<!ENTITY" in decoded.upper():
        raise ValueError("Sitemap index DTD and entities are not supported")
    declaration = re.match(r"<\?xml[^?]*\?>", decoded)
    if declaration:
        encoding = re.search(r"encoding\s*=\s*['\"]([^'\"]+)['\"]", declaration.group(), re.I)
        if encoding and encoding.group(1).lower() != "utf-8":
            raise ValueError("Sitemap index XML must declare UTF-8 encoding")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ValueError("Invalid sitemap index XML") from error
    if root.tag != _SITEMAP_NS + "sitemapindex":
        raise ValueError("Expected XML sitemapindex with standard namespace")

    filenames: list[str] = []
    seen: set[str] = set()
    public_scope: tuple | None = None
    for entry in root:
        if entry.tag != _SITEMAP_NS + "sitemap":
            raise ValueError("Unexpected sitemap index child")
        locations = [child for child in entry if child.tag == _SITEMAP_NS + "loc"]
        if len(locations) != 1 or len(locations[0]):
            raise ValueError("Each indexed sitemap needs exactly one simple loc")
        location = locations[0].text or ""
        parsed = urlsplit(location)
        # urlsplit drops empty trailing ?/# delimiters; these still violate
        # the literal, query-free local shard mapping.
        if "?" in location or "#" in location:
            raise ValueError("Indexed sitemap URL must not contain a query or fragment")
        if location != location.strip() or len(location) >= 2048 or not _is_canonical_url(location):
            raise ValueError(f"Invalid sitemap index loc URL: {location!r}")
        if "%" in parsed.path:
            raise ValueError("Indexed sitemap paths with percent encoding are unsupported")
        if any(part in (".", "..") for part in parsed.path.split("/")):
            raise ValueError("Indexed sitemap paths must use a safe same-directory location")
        prefix, _, filename = parsed.path.rpartition("/")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.xml(?:\.gz)?", filename, re.I):
            raise ValueError("Indexed sitemap must use a literal XML or XML.gz filename")
        current_scope = (*_page_url_identity(location)[:3], prefix)
        if public_scope is not None and current_scope != public_scope:
            raise ValueError("Indexed sitemap URLs must share the same site and directory")
        public_scope = current_scope
        folded = filename.casefold()
        if folded in seen:
            raise ValueError(f"Duplicate indexed sitemap filename: {filename}")
        seen.add(folded)
        if len(seen) > _MAX_SITEMAP_FILES:
            raise ValueError(f"Sitemap index supports at most {_MAX_SITEMAP_FILES} local shards")
        filenames.append(filename)
    if not filenames:
        raise ValueError(f"Sitemap index requires 1 to {_MAX_SITEMAP_FILES} local shards")

    # Validate the complete public index before opening any referenced file.
    actual_names = {path.name for path in parent.iterdir()}
    files: list[Path] = []
    for filename in filenames:
        # Windows is often case-insensitive, but deployed routes may not be.
        if filename not in actual_names:
            raise ValueError(f"Missing exact-case local indexed sitemap: {filename}")
        shard = parent / filename
        if shard.is_symlink() or not shard.is_file():
            raise ValueError(f"Indexed sitemap must be a regular local file: {filename}")
        if not shard.resolve(strict=True).is_relative_to(parent):
            raise ValueError("Indexed sitemap must stay beside the local index")
        files.append(shard)
    assert public_scope is not None
    return tuple(files), public_scope[:3]


def _read_sitemap(path: Path, remaining_bytes: int) -> tuple[dict[tuple, str], int]:
    """Read a bounded UTF-8 URL set, returning its actual byte count."""
    if path.suffix.lower() == ".gz":
        # Bound BOTH the compressed input and decompressed output before parsing.
        with path.open("rb") as stream:
            compressed = stream.read(_MAX_SITEMAP_BYTES + 1)
        if len(compressed) > _MAX_SITEMAP_BYTES:
            raise ValueError(f"Compressed sitemap exceeds {_MAX_SITEMAP_BYTES} bytes: {path}")
        try:
            with gzip.GzipFile(fileobj=BytesIO(compressed)) as stream:
                data = stream.read(min(_MAX_SITEMAP_BYTES, remaining_bytes) + 1)
        except (EOFError, OSError, zlib.error) as error:
            raise ValueError(f"Invalid gzip sitemap: {path}") from error
    else:
        with path.open("rb") as stream:
            data = stream.read(min(_MAX_SITEMAP_BYTES, remaining_bytes) + 1)
    if len(data) > _MAX_SITEMAP_BYTES:
        raise ValueError(f"Sitemap exceeds {_MAX_SITEMAP_BYTES} bytes: {path}")
    if len(data) > remaining_bytes:
        raise ValueError(f"Combined sitemaps exceed {_MAX_TOTAL_SITEMAP_BYTES} bytes")
    # ElementTree does not resolve external resources, and rejecting DTDs also
    # avoids entity amplification and XML parser-dependent interpretation.
    decoded = data.decode("utf-8-sig")
    if "<!DOCTYPE" in decoded.upper() or "<!ENTITY" in decoded.upper():
        raise ValueError("Sitemap DTD and entities are not supported")
    declaration = re.match(r"<\?xml[^?]*\?>", decoded)
    if declaration:
        encoding = re.search(r"encoding\s*=\s*['\"]([^'\"]+)['\"]", declaration.group(), re.I)
        if encoding and encoding.group(1).lower() != "utf-8":
            raise ValueError("Sitemap XML must declare UTF-8 encoding")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ValueError("Invalid sitemap XML") from error
    if root.tag != _SITEMAP_NS + "urlset":
        raise ValueError("Expected XML sitemap urlset with standard namespace")

    urls: dict[tuple, str] = {}
    # A single urlset must have one protocol and effective authority. A plain
    # host-only check would accept a mixed HTTP/HTTPS or nondefault-port file.
    site_scope: tuple[str, str, int | None] | None = None
    for entry in root:
        if entry.tag != _SITEMAP_NS + "url":
            raise ValueError("Unexpected sitemap urlset child")
        locations = [child for child in entry if child.tag == _SITEMAP_NS + "loc"]
        if len(locations) != 1 or len(locations[0]):
            raise ValueError("Each sitemap url needs exactly one simple loc")
        location = locations[0].text or ""
        if (
            location != location.strip()
            or len(location) >= 2048
            or not _is_canonical_url(location)
        ):
            raise ValueError(f"Invalid sitemap loc URL: {location!r}")
        identity = _page_url_identity(location)
        current_scope = identity[:3]
        if site_scope is not None and current_scope != site_scope:
            raise ValueError("Sitemap loc URLs must use the same scheme, host, and port")
        site_scope = current_scope
        if identity in urls:
            raise ValueError(f"Duplicate sitemap loc URL identity: {location!r}")
        urls[identity] = location
        if len(urls) > _MAX_SITEMAP_URLS:
            raise ValueError(f"Sitemap exceeds {_MAX_SITEMAP_URLS} URLs")
    return urls, len(data)


def _read_sitemaps(
    paths: Path | Sequence[Path], *, sitemap_index_path: Path | None = None,
) -> dict[tuple, str]:
    """Union local URL sets; optional index resolves adjacent files only."""
    index_site: tuple | None = None
    if sitemap_index_path is not None:
        if paths:
            raise ValueError("Supply either explicit sitemap files or --sitemap-index")
        paths, index_site = _local_index_shards(sitemap_index_path)
    if isinstance(paths, Path):
        files = (paths,)
    elif isinstance(paths, Sequence) and all(isinstance(p, Path) for p in paths):
        files = tuple(paths)
    else:
        raise ValueError("Sitemap inputs must be local file paths")
    if not 1 <= len(files) <= _MAX_SITEMAP_FILES:
        raise ValueError(f"Supply 1 to at most {_MAX_SITEMAP_FILES} local sitemap files")

    urls: dict[tuple, str] = {}
    seen_files: set[Path] = set()
    host: str | None = None
    total_bytes = 0
    for path in files:
        if path in seen_files:
            raise ValueError(f"Duplicate sitemap file: {path}")
        seen_files.add(path)
        shard, actual_bytes = _read_sitemap(path, _MAX_TOTAL_SITEMAP_BYTES - total_bytes)
        total_bytes += actual_bytes
        if index_site is not None and any(key[:3] != index_site for key in shard):
            raise ValueError("Indexed sitemap URL site differs from local shard page URLs")
        for identity, location in shard.items():
            current_host = urlsplit(location).hostname
            if host is not None and current_host != host:
                raise ValueError("Sitemap loc URLs must belong to the same host across files")
            host = current_host
            if identity in urls:
                raise ValueError(f"Duplicate sitemap loc URL identity across files: {location!r}")
            urls[identity] = location
            if len(urls) > _MAX_TOTAL_SITEMAP_URLS:
                raise ValueError(f"Combined sitemaps exceed {_MAX_TOTAL_SITEMAP_URLS} URLs")
    return urls


def verify_sitemap_manifest(
    manifest: object,
    sitemap_path: Path | Sequence[Path],
    *,
    require_indexability_coverage: bool = False,
    reject_noncanonical_urls: bool = False,
    reject_unreviewed_urls: bool = False,
    sitemap_index_path: Path | None = None,
) -> dict:
    """Assert only reviewed indexable/noindex intentions against local XML.

    An explicitly indexable page expects its explicitly reviewed canonical,
    when available, otherwise its own URL. An explicitly noindex page must
    not appear by its own URL. Absence of indexable is not an assertion.
    """
    if not isinstance(manifest, dict) or set(manifest) != {"version", "pages"}:
        raise ValueError("Manifest requires exactly 'version' and 'pages'")
    if type(manifest["version"]) is not int or manifest["version"] != 1:
        raise ValueError("Unsupported manifest version (expected integer 1)")
    pages = manifest["pages"]
    if not isinstance(pages, list) or not 1 <= len(pages) <= _MAX_PAGES:
        raise ValueError(f"Manifest 'pages' requires 1 to {_MAX_PAGES} entries")

    required: dict[tuple, str] = {}
    excluded: dict[tuple, str] = {}
    noncanonical: dict[tuple, str] = {}
    seen_pages: set[str] = set()
    seen_urls: set[tuple] = set()
    reviewed_urls: set[tuple] = set()
    unasserted = 0
    for entry in pages:
        if not isinstance(entry, dict) or set(entry) != {"path", "contract"}:
            raise ValueError("Each page requires exactly 'path' and 'contract'")
        path = _manifest_path(entry["path"])
        if path in seen_pages:
            raise ValueError(f"Duplicate manifest page path: {path}")
        seen_pages.add(path)
        contract = entry["contract"]
        if (
            not isinstance(contract, dict)
            or not isinstance(contract.get("url"), str)
            or not _is_canonical_url(contract["url"])
        ):
            raise ValueError(f"Invalid contract URL for page: {path}")
        page_url = contract["url"]
        page_identity = _page_url_identity(page_url)
        if page_identity in seen_urls:
            raise ValueError(f"Duplicate contract URL identity: {page_url}")
        seen_urls.add(page_identity)
        reviewed_urls.add(page_identity)
        if "canonical" in contract and (
            not isinstance(contract["canonical"], str)
            or not _is_canonical_url(contract["canonical"])
        ):
            raise ValueError(f"Invalid canonical URL for page: {path}")
        if "canonical" in contract:
            # A declared canonical is reviewed even if it is not a separate
            # manifest page; other URLs remain outside this opt-in inventory.
            reviewed_urls.add(_page_url_identity(contract["canonical"]))

        indexable = contract.get("indexable")
        if "indexable" in contract and type(indexable) is not bool:
            raise ValueError(f"Contract indexable must be boolean for page: {path}")
        if indexable is True:
            expected = contract.get("canonical", page_url)
            required[_page_url_identity(expected)] = expected
            if (
                reject_noncanonical_urls
                and "canonical" in contract
                and page_identity != _page_url_identity(expected)
            ):
                # Only explicit, reviewed canonical aliases are forbidden.
                noncanonical[page_identity] = page_url
        elif indexable is False:
            excluded[page_identity] = page_url
        else:
            unasserted += 1

    if require_indexability_coverage and unasserted:
        raise ValueError(
            f"{unasserted} reviewed page(s) lack an explicit indexability assertion"
        )
    if required.keys() & excluded.keys():
        raise ValueError("Manifest indexability and canonical sitemap intentions conflict")
    if not required and not excluded:
        raise ValueError("Sitemap comparison needs an explicit indexable assertion")

    sitemap = _read_sitemaps(sitemap_path, sitemap_index_path=sitemap_index_path)
    missing = sorted(url for identity, url in required.items() if identity not in sitemap)
    present = sorted(url for identity, url in excluded.items() if identity in sitemap)
    aliases = sorted(url for identity, url in noncanonical.items() if identity in sitemap)
    unreviewed = (
        sorted(url for identity, url in sitemap.items() if identity not in reviewed_urls)
        if reject_unreviewed_urls else []
    )
    report = {
        "status": "fail" if missing or present or aliases or unreviewed else "pass",
        "summary": {
            "required": len(required), "excluded": len(excluded),
            "unasserted_pages": unasserted, "sitemap_urls": len(sitemap),
        },
        "missing_required": missing,
        "present_excluded": present,
    }
    # Preserve the default JSON report schema for existing consumers.
    if reject_noncanonical_urls:
        report["present_noncanonical"] = aliases
    if reject_unreviewed_urls:
        report["unreviewed_urls"] = unreviewed
    return report
