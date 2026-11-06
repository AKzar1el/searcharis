"""Offline, explicit SEO contract assertions for a built HTML page.

This module does not fetch URLs, interpret HTTP headers, or run JavaScript. A
caller must provide the trusted expected contract and the built HTML bytes.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

_ALLOWED = frozenset({
    "url", "title_present", "canonical", "indexable", "h1_min", "h1_max",
    "hreflang", "jsonld_types",
})


class _SEOParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_chunks: list[str] = []
        self.in_title = False
        self.h1_count = 0
        self.canonicals: list[str] = []
        self.hreflangs: dict[str, list[str]] = {}
        self.robots: list[str] = []
        self.jsonld_blocks: list[str] = []
        self.in_jsonld = False
        self.jsonld_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "meta" and (values.get("name") or "").lower() in {
            "robots", "googlebot"
        }:
            self.robots.append(values.get("content") or "")
        elif tag == "link":
            rels = (values.get("rel") or "").lower().split()
            href = values.get("href")
            if "canonical" in rels and href is not None:
                self.canonicals.append(href.strip())
            if "alternate" in rels and values.get("hreflang") and href is not None:
                language = values["hreflang"].strip().lower()
                self.hreflangs.setdefault(language, []).append(href.strip())
        elif tag == "script" and (values.get("type") or "").lower().split(";")[0].strip() == "application/ld+json":
            self.in_jsonld = True
            self.jsonld_chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if tag == "script" and self.in_jsonld:
            self.jsonld_blocks.append("".join(self.jsonld_chunks))
            self.in_jsonld = False
            self.jsonld_chunks = []

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_chunks.append(data)
        if self.in_jsonld:
            self.jsonld_chunks.append(data)


def _check_contract(spec: object) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("Contract must be a JSON object")
    unknown = set(spec) - _ALLOWED
    if unknown:
        raise ValueError("Unsupported contract fields: " + ", ".join(sorted(unknown)))
    url = spec.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("Contract requires a non-empty absolute 'url'")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Contract 'url' must be an absolute public-format HTTP(S) URL")
    if len(spec) < 2:
        raise ValueError("Contract must assert at least one SEO property")
    for field in ("title_present", "indexable"):
        if field in spec and not isinstance(spec[field], bool):
            raise ValueError(f"'{field}' must be a boolean")
    if "canonical" in spec and (not isinstance(spec["canonical"], str) or not spec["canonical"]):
        raise ValueError("'canonical' must be a non-empty URL string")
    for field in ("h1_min", "h1_max"):
        if field in spec and (type(spec[field]) is not int or spec[field] < 0):
            raise ValueError(f"'{field}' must be a non-negative integer")
    if "h1_min" in spec and "h1_max" in spec and spec["h1_min"] > spec["h1_max"]:
        raise ValueError("h1_min may not exceed h1_max")
    if "hreflang" in spec and (not isinstance(spec["hreflang"], dict) or not spec["hreflang"] or any(
        not isinstance(k, str) or not k or not isinstance(v, str) or not v
        for k, v in spec["hreflang"].items()
    )):
        raise ValueError("'hreflang' must be a non-empty mapping of language to URL")
    if "jsonld_types" in spec and (not isinstance(spec["jsonld_types"], list) or any(
        not isinstance(v, str) or not v for v in spec["jsonld_types"]
    )):
        raise ValueError("'jsonld_types' must be an array of non-empty strings")
    return spec


def _types(value: object) -> set[str]:
    if isinstance(value, list):
        return set().union(*(_types(item) for item in value)) if value else set()
    if not isinstance(value, dict):
        return set()
    types = value.get("@type", [])
    found = {types} if isinstance(types, str) else {
        item for item in types if isinstance(item, str)
    } if isinstance(types, list) else set()
    return found | _types(value.get("@graph", []))


def verify_contract(contract: object, html: str) -> dict:
    """Return structured findings; raise ValueError for an invalid contract."""
    spec = _check_contract(contract)
    page = _SEOParser()
    page.feed(html)
    page.close()
    url = spec["url"]
    issues: list[dict] = []

    def fail(code: str, expected: object, actual: object) -> None:
        issues.append({"code": code, "expected": expected, "actual": actual})

    if "title_present" in spec:
        actual = bool("".join(page.title_chunks).strip())
        if actual != spec["title_present"]:
            fail("title_presence", spec["title_present"], actual)
    if "canonical" in spec:
        actual = [urljoin(url, item) for item in page.canonicals]
        if actual != [spec["canonical"]]:
            fail("canonical", [spec["canonical"]], actual)
    if "indexable" in spec:
        directives = {x.strip().lower() for content in page.robots for x in content.split(",")}
        actual = not bool({"noindex", "none"} & directives)
        if actual != spec["indexable"]:
            fail("indexability_meta", spec["indexable"], actual)
    for bound, condition in (("h1_min", lambda a, b: a >= b), ("h1_max", lambda a, b: a <= b)):
        if bound in spec and not condition(page.h1_count, spec[bound]):
            fail(bound, spec[bound], page.h1_count)
    for language, expected in spec.get("hreflang", {}).items():
        actual = [urljoin(url, href) for href in page.hreflangs.get(language.lower(), [])]
        if actual != [expected]:
            fail(f"hreflang:{language}", [expected], actual)
    if "jsonld_types" in spec:
        observed: set[str] = set()
        for index, block in enumerate(page.jsonld_blocks):
            try:
                observed |= _types(json.loads(block))
            except json.JSONDecodeError:
                fail("jsonld_invalid", "valid JSON", f"block {index + 1} malformed")
        for expected_type in spec["jsonld_types"]:
            if expected_type not in observed:
                fail("jsonld_type_missing", expected_type, sorted(observed))
    return {"status": "pass" if not issues else "fail", "checked_url": url, "issues": issues}
