"""Validate explicit build-artifact to repository-source hints for SARIF.

Maps are local metadata, never evidence of a source line or live URL status.
"""

from __future__ import annotations

import json
from pathlib import Path

_MAX_BYTES = 1_000_000
_MAX_ENTRIES = 10_000


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate source-map JSON key: {key!r}")
        result[key] = value
    return result


def _relative_posix(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or value != value.strip()
        or value.startswith("/")
        or "\\" in value
        or ":" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or not value.isprintable()
    ):
        raise ValueError(f"Unsafe source-map relative path: {value!r}")
    return value


def read_source_map(file: Path, report: dict, *, source_root: Path) -> dict[str, str]:
    """Read bounded explicit mappings, requiring each source within source_root."""
    if file.stat().st_size > _MAX_BYTES:
        raise ValueError(f"Source map exceeds {_MAX_BYTES} bytes: {file}")
    mapping = json.loads(file.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(mapping, dict) or set(mapping) != {"version", "sources"}:
        raise ValueError("Source map requires exactly 'version' and 'sources'")
    if type(mapping["version"]) is not int or mapping["version"] != 1:
        raise ValueError("Unsupported source-map version (expected integer 1)")
    sources = mapping["sources"]
    if not isinstance(sources, dict) or len(sources) > _MAX_ENTRIES:
        raise ValueError(f"Source-map 'sources' must contain at most {_MAX_ENTRIES} entries")

    known_paths = {page["path"] for page in report["pages"]}
    known_paths.update(report["coverage"]["unlisted"])
    root = source_root.resolve(strict=True)
    checked: set[str] = set()
    result: dict[str, str] = {}
    for artifact, source in sources.items():
        artifact = _relative_posix(artifact)
        source = _relative_posix(source)
        # Build artifacts are distinct by exact spelling on case-sensitive CI
        # runners; the site checker and comparator preserve that distinction.
        if artifact in checked:
            raise ValueError(f"Duplicate source-map artifact path: {artifact}")
        checked.add(artifact)
        if artifact not in known_paths:
            raise ValueError(f"Source map refers to an unknown build artifact: {artifact}")
        path = (root / source).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Source map escapes source root or is not a file: {source}")
        result[artifact] = source
    return result
