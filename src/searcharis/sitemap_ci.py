"""Check explicit SEO sitemap intent using only a local XML file and reviewed manifest.

No URL from either input is requested. Unasserted pages and unrelated sitemap
entries are deliberately not treated as errors: the manifest is not a crawl map.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
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


def _read_sitemap(path: Path, remaining_bytes: int) -> tuple[dict[tuple, str], int]:
    """Read a bounded UTF-8 URL set, returning its actual byte count."""
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
    host: str | None = None
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
        current_host = urlsplit(location).hostname
        if host is not None and current_host != host:
            raise ValueError("Sitemap loc URLs must belong to the same host")
        host = current_host
        identity = _page_url_identity(location)
        if identity in urls:
            raise ValueError(f"Duplicate sitemap loc URL identity: {location!r}")
        urls[identity] = location
        if len(urls) > _MAX_SITEMAP_URLS:
            raise ValueError(f"Sitemap exceeds {_MAX_SITEMAP_URLS} URLs")
    return urls, len(data)


def _read_sitemaps(paths: Path | Sequence[Path]) -> dict[tuple, str]:
    """Union explicitly supplied local URL sets without resolving index URLs."""
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


def verify_sitemap_manifest(manifest: object, sitemap_path: Path | Sequence[Path]) -> dict:
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
    seen_pages: set[str] = set()
    seen_urls: set[tuple] = set()
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
        if "canonical" in contract and (
            not isinstance(contract["canonical"], str)
            or not _is_canonical_url(contract["canonical"])
        ):
            raise ValueError(f"Invalid canonical URL for page: {path}")

        indexable = contract.get("indexable")
        if "indexable" in contract and type(indexable) is not bool:
            raise ValueError(f"Contract indexable must be boolean for page: {path}")
        if indexable is True:
            expected = contract.get("canonical", page_url)
            required[_page_url_identity(expected)] = expected
        elif indexable is False:
            excluded[page_identity] = page_url
        else:
            unasserted += 1

    if required.keys() & excluded.keys():
        raise ValueError("Manifest indexability and canonical sitemap intentions conflict")
    if not required and not excluded:
        raise ValueError("Sitemap comparison needs an explicit indexable assertion")

    sitemap = _read_sitemaps(sitemap_path)
    missing = sorted(url for identity, url in required.items() if identity not in sitemap)
    present = sorted(url for identity, url in excluded.items() if identity in sitemap)
    return {
        "status": "fail" if missing or present else "pass",
        "summary": {
            "required": len(required), "excluded": len(excluded),
            "unasserted_pages": unasserted, "sitemap_urls": len(sitemap),
        },
        "missing_required": missing,
        "present_excluded": present,
    }
