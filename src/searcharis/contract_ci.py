"""Offline, explicit SEO contract assertions for a built HTML page.

This module does not fetch URLs, interpret HTTP headers, or run JavaScript. A
caller must provide the trusted expected contract and the built HTML bytes.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from math import isfinite
from urllib.parse import urljoin, urlsplit

_ALLOWED = frozenset({
    "url", "title_present", "description_present", "canonical", "indexable", "h1_min", "h1_max",
    "hreflang", "jsonld_types",
})
_HEAD_ELEMENTS = frozenset({
    "base", "link", "meta", "noscript", "script", "style", "template", "title",
})
_RAWTEXT_ELEMENTS = frozenset({"iframe", "xmp", "noembed", "noframes", "noscript"})
_NONVOID_STATE_ELEMENTS = _RAWTEXT_ELEMENTS | frozenset({
    "head", "script", "style", "template", "textarea", "title", "plaintext", "select",
})
_MAX_JSONLD_NESTING = 256


class _SEOParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_html = False
        self.has_head = False
        self.in_head = False
        self.body_seen = False
        self.title_chunks: list[str] = []
        self.in_title = False
        self.in_title_element = False
        self.title_seen = False
        self.description_content: str | None = None
        self.h1_count = 0
        self.canonicals: list[str] = []
        self.hreflangs: dict[str, list[str]] = {}
        self.robots: list[str] = []
        self.base_href: str | None = None
        self.jsonld_blocks: list[str] = []
        self.in_jsonld = False
        self.jsonld_chunks: list[str] = []
        self.template_depth = 0
        self.in_textarea = False
        self.in_plaintext = False

    def set_cdata_mode(self, elem: str, **kwargs: bool) -> None:
        # HTMLParser tokenizes markup inside RCDATA and some raw-text elements,
        # unlike browsers. A literal <script> must not hide their closing tag.
        if not self.in_textarea and not self.in_title_element:
            super().set_cdata_mode(elem, **kwargs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # In HTML (unlike XML), /> does not close a non-void element. HTMLParser
        # otherwise calls handle_endtag immediately, losing parser state and
        # letting inert markup masquerade as live SEO signals.
        self.handle_starttag(tag, attrs)
        if tag in {"script", "style"} and not (
            self.in_textarea or self.in_title_element or self.in_plaintext
        ):
            # HTMLParser enters CDATA only for ordinary start tags; the same
            # raw-text state is required for a non-void <script/> or <style/>.
            self.set_cdata_mode(tag)
        if tag not in _NONVOID_STATE_ELEMENTS:
            self.handle_endtag(tag)

    def handle_decl(self, decl: str) -> None:
        if self.in_textarea or self.in_plaintext:
            return
        # A full HTML document may legally omit its <html> start tag.
        if decl.lower().split()[:2] == ["doctype", "html"]:
            self.has_html = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.in_textarea or self.in_title_element or self.in_plaintext:
            return  # Markup in text-only contexts is not active page content.
        if tag == "template":
            if self.has_html and not self.has_head and not self.body_seen:
                self.has_head = self.in_head = True
            self.template_depth += 1
            return
        if tag == "plaintext" and self.template_depth:
            # Even an inert template cannot terminate the plaintext tokenizer;
            # all following apparent tags, including </template>, are text.
            self.in_plaintext = True
            return
        if self.template_depth:
            if tag in _RAWTEXT_ELEMENTS | {"title", "textarea"}:
                # Template contents are inert, but the HTML tokenizer still
                # treats RCDATA/RAWTEXT end-tag lookalikes as literal text.
                # Otherwise a fake </template> exposes later ghost SEO tags.
                self.set_cdata_mode(tag)
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
        elif self.has_html and not self.has_head and not self.body_seen:
            # HTML5 allows an omitted <head> start tag, commonly emitted by
            # minifiers. The first metadata element opens an implicit head.
            if tag in _HEAD_ELEMENTS:
                self.has_head = self.in_head = True
            elif tag != "html":
                self.body_seen = True
        elif self.in_head and tag not in _HEAD_ELEMENTS and tag != "head":
            # An omitted </head>/<body> must not let body metadata pass checks.
            self.in_head = False
            self.body_seen = True
            self.in_title = False
        if tag == "body":
            self.body_seen = True
            self.in_head = False  # HTML can omit an explicit closing </head>.
            self.in_title = False
        if tag == "textarea":
            self.in_textarea = True
        elif tag == "plaintext":
            # HTML treats everything after <plaintext> as text, including a
            # literal </plaintext>; no later tag can become an SEO signal.
            self.in_plaintext = True
        elif tag == "title":
            self.in_title_element = True
        elif tag in _RAWTEXT_ELEMENTS:
            # HTMLParser does not natively tokenize these HTML raw-text
            # elements as text. Scripting-enabled <noscript> is also raw text,
            # so its fallback markup cannot spoof active page SEO signals.
            self.set_cdata_mode(tag)
        if tag == "title" and self.in_head and not self.title_seen:
            # The first head title is the document title; later duplicates
            # must not turn an empty first title into a false presence pass.
            self.title_seen = True
            self.in_title = True
        elif tag == "base" and self.in_head and self.base_href is None and "href" in values:
            # HTML uses the first head <base href> to resolve relative links.
            self.base_href = (values["href"] or "").strip()
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "meta" and self.in_head:
            name = (values.get("name") or "").lower()
            if name in {"robots", "googlebot"}:
                self.robots.append(values.get("content") or "")
            elif name == "description" and self.description_content is None:
                # The first head description is authoritative for this opt-in
                # policy; a later duplicate cannot mask an empty first value.
                self.description_content = values.get("content") or ""
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
        if self.in_plaintext:
            return
        if self.in_textarea:
            if tag == "textarea":
                self.in_textarea = False
            return
        if self.in_title_element:
            if tag == "title":
                self.in_title_element = False
                self.in_title = False
            return
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
        if self.template_depth or self.in_textarea or self.in_plaintext:
            return
        if self.in_title_element:
            if self.in_title:
                self.title_chunks.append(data)
            return
        if self.has_html and not self.body_seen and not self.cdata_elem and data.strip(" \t\n\r\f"):
            # Visible text starts the body even when <head> was explicitly
            # opened or implicitly opened by preceding metadata. Script/style
            # raw text, title RCDATA and inert template content are excluded.
            self.has_head = True
            self.in_head = False
            self.body_seen = True
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
    # A malformed declared URL can make title-only contracts falsely pass and
    # corrupt the URL identities used by cross-page consistency checks.
    if not _is_http_url(url):
        raise ValueError("Contract 'url' must be an absolute public-format HTTP(S) URL")
    if len(spec) < 2:
        raise ValueError("Contract must assert at least one SEO property")
    for field in ("title_present", "description_present", "indexable"):
        if field in spec and not isinstance(spec[field], bool):
            raise ValueError(f"'{field}' must be a boolean")
    if "canonical" in spec and (
        not isinstance(spec["canonical"], str) or not _is_http_url(spec["canonical"])
    ):
        raise ValueError("'canonical' must be an absolute HTTP(S) URL")
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
    if "hreflang" in spec and any(not _is_http_url(v) for v in spec["hreflang"].values()):
        raise ValueError("'hreflang' values must be absolute HTTP(S) URLs")
    if "jsonld_types" in spec and (not isinstance(spec["jsonld_types"], list) or not spec["jsonld_types"] or any(
        not isinstance(v, str) or not v.strip() for v in spec["jsonld_types"]
    )):
        raise ValueError("'jsonld_types' must be a non-empty array of non-blank strings")
    return spec


def _types(value: object) -> set[str]:
    found: set[str] = set()
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, dict):
            # @context describes terms/type coercion, not page entities.
            # @value objects are literals (including @json with nested JSON),
            # so their contents must not masquerade as structured-data nodes.
            if "@value" in current:
                continue
            types = current.get("@type", [])
            if isinstance(types, str):
                found.add(types)
            elif isinstance(types, list):
                found.update(item for item in types if isinstance(item, str))
            pending.extend(item for key, item in current.items() if key not in {"@context", "@type"})
    return found


def _reject_non_json_number(value: str) -> None:
    # Python accepts NaN/Infinity by default, but JSON-LD requires valid JSON.
    raise ValueError(f"Non-JSON numeric constant: {value}")


def _finite_json_float(value: str) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"JSON-LD number exceeds finite float range: {value}")
    return number


def _unique_jsonld_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON-LD key: {key}")
        result[key] = value
    return result


def _jsonld_too_deep(raw: str) -> bool:
    # Limit decoder nesting consistently on Python versions with different
    # recursion headroom. Brackets inside JSON strings are not containers.
    depth = 0
    in_string = False
    escaped = False
    for char in raw:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > _MAX_JSONLD_NESTING:
                return True
        elif char in "]}":
            depth -= 1
    return False


def _resolve_href(base_url: str, href: str) -> str:
    # An empty <link href> is not a URL, even though urljoin maps it to base_url.
    if not href:
        return ""
    try:
        return urljoin(base_url, href)
    except ValueError:
        # Malformed built HTML is a failed assertion, not an invalid contract.
        return href


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and (parsed.port is None or 0 < parsed.port <= 65535)
            # Browsers interpret backslashes as slashes in HTTP(S) URLs,
            # while urlsplit preserves them and may infer a different host.
            and "\\" not in value
            and not any(char.isspace() or ord(char) < 32 for char in value)
        )
    except ValueError:
        return False


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
            valid_base = _is_http_url(candidate)
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
    if "description_present" in spec:
        actual = bool((page.description_content or "").strip())
        if actual != spec["description_present"]:
            fail("description_presence", spec["description_present"], actual)
    if "canonical" in spec:
        actual = [_resolve_href(base_url, item) for item in page.canonicals]
        if actual != [spec["canonical"]] or any(not _is_http_url(item) for item in actual):
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
        actual = [_resolve_href(base_url, href) for href in page.hreflangs.get(language.lower(), [])]
        if actual != [expected] or any(not _is_http_url(item) for item in actual):
            fail(f"hreflang:{language}", [expected], actual)
    if "jsonld_types" in spec:
        observed: set[str] = set()
        for index, block in enumerate(page.jsonld_blocks):
            try:
                if _jsonld_too_deep(block):
                    raise ValueError("JSON-LD exceeds supported nesting depth")
                observed |= _types(json.loads(
                    block,
                    parse_constant=_reject_non_json_number,
                    parse_float=_finite_json_float,
                    object_pairs_hook=_unique_jsonld_keys,
                ))
            except (json.JSONDecodeError, ValueError, RecursionError):
                # Invalid input and decoder recursion exhaustion are findings,
                # never a traceback that breaks JSON-mode CI consumers.
                fail("jsonld_invalid", "valid JSON", f"block {index + 1} malformed")
        for expected_type in spec["jsonld_types"]:
            if expected_type not in observed:
                fail("jsonld_type_missing", expected_type, sorted(observed))
    resolved_hreflang: dict[str, list[str]] = {}
    if include_hreflang:
        for language, hrefs in sorted(page.hreflangs.items()):
            valid_targets = []
            if "" in hrefs:
                fail(f"hreflang_empty_href:{language}", "non-empty href", hrefs)
            for href in hrefs:
                if href:
                    target = _resolve_href(base_url, href)
                    if _is_http_url(target):
                        valid_targets.append(target)
                    else:
                        fail(f"hreflang_invalid_url:{language}", "absolute HTTP(S) URL", target)
            resolved_hreflang[language] = valid_targets
    result = {"status": "pass" if not issues else "fail", "checked_url": url, "issues": issues}
    if include_hreflang:
        # Site-level checks need the actual built links, not just contract expectations.
        result["observed_hreflang"] = resolved_hreflang
    if include_canonical:
        # Expose observed HTML values for optional cross-page checks; never fetch URLs.
        result["observed_canonical"] = [_resolve_href(base_url, href) for href in page.canonicals]
    return result
