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
        self.has_html = False
        self.has_head = False
        self.in_head = False
        self.body_seen = False
        self.title_chunks: list[str] = []
        self.in_title = False
        self.h1_count = 0
        self.canonicals: list[str] = []
        self.hreflangs: dict[str, list[str]] = {}
        self.robots: list[str] = []
        self.base_href: str | None = None
        self.jsonld_blocks: list[str] = []
        self.in_jsonld = False
        self.jsonld_chunks: list[str] = []
        self.template_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "template":
            self.template_depth += 1
            return
        if self.template_depth:
            return  # Inert template descendants are not active page SEO signals.
        # HTML parsing retains the first occurrence of a duplicated attribute.
        # dict(attrs) would silently let a later value spoof SEO assertions.
        values: dict[str, str | None] = {}
        for name, value in attrs:
            values.setdefault(name, value)
        if tag == "html":
            self.has_html = True
        if tag == "head" and not self.body_seen:
            self.has_head = True
            self.in_head = True
        if tag == "body":
            self.body_seen = True
            self.in_head = False  # HTML can omit an explicit closing </head>.
            self.in_title = False
        if tag == "title" and self.in_head:
            self.in_title = True
        elif tag == "base" and self.in_head and self.base_href is None and "href" in values:
            # HTML uses the first head <base href> to resolve relative links.
            self.base_href = (values["href"] or "").strip()
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "meta" and self.in_head and (values.get("name") or "").lower() in {
            "robots", "googlebot"
        }:
            self.robots.append(values.get("content") or "")
        elif tag == "link" and self.in_head:
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
        if tag == "template":
            self.template_depth = max(0, self.template_depth - 1)
            return
        if self.template_depth:
            return
        if tag == "head":
            self.in_head = False
            self.in_title = False
        if tag == "title":
            self.in_title = False
        if tag == "script" and self.in_jsonld:
            self.jsonld_blocks.append("".join(self.jsonld_chunks))
            self.in_jsonld = False
            self.jsonld_chunks = []

    def handle_data(self, data: str) -> None:
        if self.template_depth:
            return
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


def verify_contract(
    contract: object,
    html: str,
    *,
    include_hreflang: bool = False,
    include_canonical: bool = False,
) -> dict:
    """Return structured findings; raise ValueError for an invalid contract."""
    spec = _check_contract(contract)
    page = _SEOParser()
    page.feed(html)
    page.close()
    url = spec["url"]
    issues: list[dict] = []

    def fail(code: str, expected: object, actual: object) -> None:
        issues.append({"code": code, "expected": expected, "actual": actual})

    base_url = url
    if page.base_href is not None:
        try:
            candidate = urljoin(url, page.base_href)
            parsed_base = urlsplit(candidate)
            valid_base = (
                parsed_base.scheme in {"http", "https"}
                and bool(parsed_base.hostname)
                and parsed_base.username is None
                and parsed_base.password is None
                and (parsed_base.port is None or 0 < parsed_base.port <= 65535)
                and not any(char.isspace() or ord(char) < 32 for char in candidate)
            )
        except ValueError:
            valid_base = False
        if valid_base:
            base_url = candidate
        else:
            fail("document_base_invalid", "an HTTP(S) document base URL", page.base_href)

    if not page.has_html or not page.has_head:
        fail("html_structure", "HTML document with head", {"html": page.has_html, "head": page.has_head})
    if "title_present" in spec:
        actual = bool("".join(page.title_chunks).strip())
        if actual != spec["title_present"]:
            fail("title_presence", spec["title_present"], actual)
    if "canonical" in spec:
        actual = [urljoin(base_url, item) for item in page.canonicals]
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
        actual = [urljoin(base_url, href) for href in page.hreflangs.get(language.lower(), [])]
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
    result = {"status": "pass" if not issues else "fail", "checked_url": url, "issues": issues}
    if include_hreflang:
        # Site-level checks need the actual built links, not just contract expectations.
        result["observed_hreflang"] = {
            language: [urljoin(base_url, href) for href in hrefs if href]
            for language, hrefs in sorted(page.hreflangs.items())
        }
    if include_canonical:
        # Expose observed HTML values for optional cross-page checks; never fetch URLs.
        observed = []
        for href in page.canonicals:
            try:
                observed.append(urljoin(base_url, href) if href else "")
            except ValueError:
                observed.append(href)  # Malformed URLs become assertion failures.
        result["observed_canonical"] = observed
    return result
