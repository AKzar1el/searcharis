"""Offline, explicit SEO contract assertions for a built HTML page.

This module does not fetch URLs, interpret HTTP headers, or run JavaScript. A
caller must provide the trusted expected contract and the built HTML bytes.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from math import isfinite
from urllib.parse import urljoin, urlsplit

_ALLOWED = frozenset({
    "url", "title_present", "title_equals", "description_present", "description_equals",
    "og_type_equals", "og_locale_equals", "og_locale_alternates", "og_site_name_equals", "og_title_equals",
    "og_description_equals", "og_url_equals",
    "og_image_present", "og_image_equals",
    "og_image_alt_present", "og_image_alt_equals", "twitter_card_equals", "twitter_title_equals", "twitter_description_equals",
    "og_image_width_equals", "og_image_height_equals", "og_image_type_equals",
    "twitter_image_present", "twitter_image_equals", "twitter_image_alt_present", "twitter_image_alt_equals",
    "viewport_device_width", "meta_refresh_present", "html_lang", "canonical", "canonical_present",
    "indexable", "followable",
    "h1_min", "h1_max", "h1_text_equals",
    "img_missing_alt_max", "hreflang", "jsonld_types",
})
_HEAD_ELEMENTS = frozenset({
    "base", "link", "meta", "noscript", "script", "style", "template", "title",
})
_RAWTEXT_ELEMENTS = frozenset({"iframe", "xmp", "noembed", "noframes", "noscript"})
_NONVOID_STATE_ELEMENTS = _RAWTEXT_ELEMENTS | frozenset({
    "head", "h1", "script", "style", "template", "textarea", "title", "plaintext", "select",
})
_TWITTER_CARD_TYPES = frozenset({"summary", "summary_large_image", "app", "player"})
_IMAGE_MIME = re.compile(r"image/[a-z0-9][a-z0-9.+_-]*", re.IGNORECASE | re.ASCII)
_MAX_JSONLD_NESTING = 256


class _SEOParser(HTMLParser):
    def __init__(self, *, collect_anchors: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.collect_anchors = collect_anchors
        self.anchors: list[str] = []
        self.has_html = False
        self.html_tag_seen = False
        self.html_lang: str | None = None
        self.has_head = False
        self.in_head = False
        self.body_seen = False
        self.title_chunks: list[str] = []
        self.in_title = False
        self.in_title_element = False
        self.title_seen = False
        self.description_content: str | None = None
        self.og_type_content: str | None = None
        self.og_locale_content: str | None = None
        self.og_locale_alternates: list[str] = []
        self.og_site_name_content: str | None = None
        self.og_title_content: str | None = None
        self.og_description_content: str | None = None
        self.og_url_content: str | None = None
        self.og_image_content: str | None = None
        self.og_image_alt_content: str | None = None
        self.og_image_width_content: str | None = None
        self.og_image_height_content: str | None = None
        self.og_image_type_content: str | None = None
        self.twitter_card_content: str | None = None
        self.twitter_title_content: str | None = None
        self.twitter_description_content: str | None = None
        self.twitter_image_content: str | None = None
        self.twitter_image_alt_content: str | None = None
        self._first_og_image_open = False
        self.viewport_content: str | None = None
        self.meta_refresh_contents: list[str] = []
        self.h1_count = 0
        self.first_h1_chunks: list[str] = []
        self._first_h1_seen = False
        self._in_first_h1 = False
        self.img_missing_alt_count = 0
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
        if self.collect_anchors and tag == "a" and "href" in values:
            if len(self.anchors) >= 20_000:
                raise ValueError("Built HTML exceeds 20000 navigational links")
            self.anchors.append((values["href"] or "").strip())
        if tag == "html":
            if not self.html_tag_seen and not self.has_head and not self.body_seen:
                # Only the root start tag may supply the declared document language.
                self.html_lang = (values.get("lang") or "") if "lang" in values else None
                self.html_tag_seen = True
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
                # A body element implies a valid, empty <head> when the document
                # omits head metadata altogether; it does not open metadata scope.
                self.has_head = True
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
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            if self._in_first_h1:
                # Another heading start implicitly ends the current heading,
                # even if the source omits its closing </h1> tag.
                self._in_first_h1 = False
            if tag == "h1":
                self.h1_count += 1
                if not self._first_h1_seen:
                    self._first_h1_seen = self._in_first_h1 = True
        elif tag == "img" and "alt" not in values:
            # An empty alt="" is valid for decorative images; absence is
            # different from a deliberately empty attribute.
            self.img_missing_alt_count += 1
        elif tag == "meta":
            name = (values.get("name") or "").lower()
            property_name = values.get("property") or ""
            if self.in_head and (values.get("http-equiv") or "").strip().lower() == "refresh":
                # Track declarations, including malformed/blank values, so a
                # forbidden refresh cannot silently pass a publishing contract.
                self.meta_refresh_contents.append(values.get("content") or "")
            if (self.in_head and self._first_og_image_open and property_name
                    and not property_name.startswith("og:image:")):
                # Open Graph ends a structured-property group at the next root.
                # An alt after og:title, for example, is not for the first image.
                self._first_og_image_open = False
            if name in {"robots", "googlebot"}:
                # Google honors robots meta in the body as well as the head.
                self.robots.append(values.get("content") or "")
            elif name == "viewport" and self.in_head and self.viewport_content is None:
                # A later duplicate or body metadata cannot rescue a bad first
                # head viewport policy.
                self.viewport_content = values.get("content") or ""
            elif name == "description" and self.in_head and self.description_content is None:
                # The first head description is authoritative for this opt-in
                # policy; a later duplicate cannot mask an empty first value.
                self.description_content = values.get("content") or ""
            elif name == "twitter:card" and self.in_head and self.twitter_card_content is None:
                # The first active card type governs this explicit publish contract.
                self.twitter_card_content = (values.get("content") or "").strip()
            elif name == "twitter:title" and self.in_head and self.twitter_title_content is None:
                # Only the first explicit head title is authoritative; no OG fallback.
                self.twitter_title_content = values.get("content") or ""
            elif (name == "twitter:description" and self.in_head
                  and self.twitter_description_content is None):
                # Do not assume the Open Graph fallback supplies explicit X copy.
                self.twitter_description_content = values.get("content") or ""
            elif name == "twitter:image" and self.in_head and self.twitter_image_content is None:
                # Guard only the first explicit X image, not an Open Graph fallback.
                self.twitter_image_content = (values.get("content") or "").strip()
            elif (name == "twitter:image:alt" and self.in_head
                  and self.twitter_image_alt_content is None):
                # Explicit X alt copy is independent of an Open Graph fallback.
                self.twitter_image_alt_content = values.get("content") or ""
            if (self.in_head and values.get("property") == "og:type"
                    and self.og_type_content is None):
                self.og_type_content = (values.get("content") or "").strip()
            if (self.in_head and values.get("property") == "og:locale"
                    and self.og_locale_content is None):
                self.og_locale_content = (values.get("content") or "").strip()
            if self.in_head and values.get("property") == "og:locale:alternate":
                # Alternative locales are repeatable; retain duplicates so they
                # cannot silently satisfy a set-shaped publishing contract.
                self.og_locale_alternates.append((values.get("content") or "").strip())
            if (self.in_head and values.get("property") == "og:site_name"
                    and self.og_site_name_content is None):
                self.og_site_name_content = values.get("content") or ""
            if (self.in_head and values.get("property") == "og:title"
                    and self.og_title_content is None):
                # The first active Open Graph title is the publishing contract;
                # a later duplicate cannot rescue a blank or outdated value.
                self.og_title_content = values.get("content") or ""
            if (self.in_head and values.get("property") == "og:description"
                    and self.og_description_content is None):
                self.og_description_content = values.get("content") or ""
            if self.in_head and values.get("property") == "og:url" and self.og_url_content is None:
                # Open Graph uses the first og:url as the shared object's ID.
                # A later duplicate cannot conceal identity drift or an empty value.
                self.og_url_content = (values.get("content") or "").strip()
            if self.in_head and values.get("property") in {"og:image", "og:image:url"}:
                if self.og_image_content is None:
                    # Structured og:image:alt belongs to this first image only.
                    self.og_image_content = (values.get("content") or "").strip()
                    self._first_og_image_open = True
                else:
                    # A second root starts a separate image, even if the first
                    # root had an empty URL or no alternative description.
                    self._first_og_image_open = False
            if (self.in_head and values.get("property") == "og:image:alt"
                    and self._first_og_image_open and self.og_image_alt_content is None):
                self.og_image_alt_content = (values.get("content") or "").strip()
            if self.in_head and self._first_og_image_open:
                for property_name, attribute in (
                    ("og:image:width", "og_image_width_content"),
                    ("og:image:height", "og_image_height_content"),
                    ("og:image:type", "og_image_type_content"),
                ):
                    if values.get("property") == property_name and getattr(self, attribute) is None:
                        setattr(self, attribute, (values.get("content") or "").strip())
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
        if tag == "h1":
            self._in_first_h1 = False
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
        if self._in_first_h1 and not self.cdata_elem:
            # HTMLParser decodes entities; do not include script/style raw text.
            self.first_h1_chunks.append(data)
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
    # Search engines do not use fragment identifiers as distinct page URLs.
    # Reject even an empty trailing '#' so two contracts cannot claim separate
    # indexable page identities based only on different URL fragments.
    if "#" in url:
        raise ValueError("Contract 'url' must not contain a URL fragment")
    if len(spec) < 2:
        raise ValueError("Contract must assert at least one SEO property")
    for field in ("title_present", "description_present", "viewport_device_width", "meta_refresh_present",
                  "canonical_present", "indexable", "followable", "og_image_present", "og_image_alt_present",
                  "twitter_image_present", "twitter_image_alt_present"):
        if field in spec and not isinstance(spec[field], bool):
            raise ValueError(f"'{field}' must be a boolean")
    for field in ("title_equals", "description_equals", "og_type_equals", "og_locale_equals",
                  "og_site_name_equals",
                  "og_title_equals", "og_description_equals", "og_image_alt_equals",
                  "twitter_title_equals", "twitter_description_equals", "twitter_image_alt_equals",
                  "h1_text_equals"):
        if field in spec and (not isinstance(spec[field], str) or not spec[field].strip()):
            raise ValueError(f"'{field}' must be non-blank text")
    if "twitter_card_equals" in spec and (
        not isinstance(spec["twitter_card_equals"], str)
        or spec["twitter_card_equals"] not in _TWITTER_CARD_TYPES
    ):
        raise ValueError("'twitter_card_equals' must be a supported Twitter Card type")
    if "og_locale_alternates" in spec:
        alternates = spec["og_locale_alternates"]
        if (not isinstance(alternates, list)
                or any(not isinstance(item, str) or not item or item != item.strip()
                       or any(char.isspace() for char in item) for item in alternates)
                or len(set(alternates)) != len(alternates)):
            raise ValueError("'og_locale_alternates' must be an array of distinct non-blank locales")
    if "html_lang" in spec and (
        not isinstance(spec["html_lang"], str)
        or not spec["html_lang"]
        or any(char.isspace() for char in spec["html_lang"])
    ):
        raise ValueError("'html_lang' must be a non-empty language tag without whitespace")
    if "canonical" in spec and (
        not isinstance(spec["canonical"], str) or not _is_canonical_url(spec["canonical"])
    ):
        raise ValueError("'canonical' must be an absolute HTTP(S) URL without fragments")
    if "og_image_equals" in spec and (
        not isinstance(spec["og_image_equals"], str) or not _is_http_url(spec["og_image_equals"])
    ):
        raise ValueError("'og_image_equals' must be an absolute HTTP(S) URL")
    if "twitter_image_equals" in spec and (
        not isinstance(spec["twitter_image_equals"], str)
        or not _is_http_url(spec["twitter_image_equals"])
    ):
        raise ValueError("'twitter_image_equals' must be an absolute HTTP(S) URL")
    if "og_url_equals" in spec and (
        not isinstance(spec["og_url_equals"], str) or not _is_http_url(spec["og_url_equals"])
    ):
        raise ValueError("'og_url_equals' must be an absolute HTTP(S) URL")
    for field in ("h1_min", "h1_max", "img_missing_alt_max"):
        if field in spec and (type(spec[field]) is not int or spec[field] < 0):
            raise ValueError(f"'{field}' must be a non-negative integer")
    for field in ("og_image_width_equals", "og_image_height_equals"):
        if field in spec and (type(spec[field]) is not int or spec[field] <= 0):
            raise ValueError(f"'{field}' must be a positive integer number of pixels")
    if "og_image_type_equals" in spec and (
        not isinstance(spec["og_image_type_equals"], str)
        or _IMAGE_MIME.fullmatch(spec["og_image_type_equals"]) is None
    ):
        raise ValueError("'og_image_type_equals' must be an image MIME type (for example image/png)")
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


def _resolve_canonical_href(base_url: str, href: str) -> str:
    resolved = _resolve_href(base_url, href)
    # urljoin discards an empty trailing '#' even though it is still an
    # unsupported canonical fragment; retain that evidence for the checker.
    return resolved + "#" if "#" in href and "#" not in resolved else resolved


def _viewport_uses_device_width(content: str) -> bool:
    widths = []
    for directive in content.split(","):
        key, separator, value = directive.partition("=")
        if separator and key.strip().lower() == "width":
            widths.append(value.strip().lower())
    # Reject ambiguous duplicate width settings rather than accepting a
    # device-width token that may not be the effective browser instruction.
    return widths == ["device-width"]


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and (parsed.port is None or 0 < parsed.port <= 65535)
            # urlsplit treats an explicit empty port as None. A dangling ':'
            # is not a valid declared authority, including after IPv6 ']'.
            and not parsed.netloc.endswith(":")
            # Browsers percent-decode hosts before interpreting them, while
            # urlsplit leaves encoded authority untouched. Do not accept a
            # hostname that could resolve differently from the asserted URL.
            and "%" not in parsed.netloc
            # Browsers interpret backslashes as slashes in HTTP(S) URLs,
            # while urlsplit preserves them and may infer a different host.
            and "\\" not in value
            and not any(char.isspace() or ord(char) < 32 for char in value)
        )
    except ValueError:
        return False


def _is_canonical_url(value: str) -> bool:
    # Google generally ignores URL fragments for rel=canonical. A bare '#'
    # is rejected too, while percent-encoded '#' within a path stays valid.
    return _is_http_url(value) and "#" not in value


def verify_contract(
    contract: object,
    html: str,
    *,
    include_hreflang: bool = False,
    include_canonical: bool = False,
    include_anchors: bool = False,
) -> dict:
    """Return structured findings; raise ValueError for an invalid contract."""
    spec = _check_contract(contract)
    page = _SEOParser(collect_anchors=include_anchors)
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
    if "title_equals" in spec:
        # HTML entities are decoded by HTMLParser; ignore presentation-only
        # whitespace changes while retaining case and meaningful title text.
        actual = " ".join("".join(page.title_chunks).split())
        if actual != " ".join(spec["title_equals"].split()):
            fail("title_equals", spec["title_equals"], actual)
    if "description_present" in spec:
        actual = bool((page.description_content or "").strip())
        if actual != spec["description_present"]:
            fail("description_presence", spec["description_present"], actual)
    if "description_equals" in spec:
        # Compare the first head description, not a duplicate or body fallback.
        actual = " ".join((page.description_content or "").split())
        if actual != " ".join(spec["description_equals"].split()):
            fail("description_equals", spec["description_equals"], actual)
    if "og_type_equals" in spec and page.og_type_content != spec["og_type_equals"].strip():
        # Guard the declared sharing object class independently of text metadata.
        fail("og_type_equals", spec["og_type_equals"], page.og_type_content)
    if "og_locale_equals" in spec and page.og_locale_content != spec["og_locale_equals"].strip():
        fail("og_locale_equals", spec["og_locale_equals"], page.og_locale_content)
    if "og_locale_alternates" in spec:
        # Locale alternatives form an unordered multivalue declaration; a
        # duplicate is a regression even when every expected locale is present.
        expected = sorted(spec["og_locale_alternates"])
        actual = sorted(page.og_locale_alternates)
        if actual != expected:
            fail("og_locale_alternates", expected, actual)
    if "og_site_name_equals" in spec:
        # The first explicit social-preview site label is a publishing contract;
        # do not infer the brand from the host, title or a later duplicate.
        actual = (None if page.og_site_name_content is None
                  else " ".join(page.og_site_name_content.split()))
        if actual != " ".join(spec["og_site_name_equals"].split()):
            fail("og_site_name_equals", spec["og_site_name_equals"], actual)
    if "og_title_equals" in spec:
        # Social preview metadata can drift while the document title stays put.
        actual = " ".join((page.og_title_content or "").split())
        if actual != " ".join(spec["og_title_equals"].split()):
            fail("og_title_equals", spec["og_title_equals"], actual)
    if "og_description_equals" in spec:
        # Open Graph copy can drift independently of the search meta description.
        actual = " ".join((page.og_description_content or "").split())
        if actual != " ".join(spec["og_description_equals"].split()):
            fail("og_description_equals", spec["og_description_equals"], actual)
    if "og_url_equals" in spec and page.og_url_content != spec["og_url_equals"]:
        # Preserve URL identity exactly; a changed social canonical is a regression.
        fail("og_url_equals", spec["og_url_equals"], page.og_url_content)
    if "og_image_present" in spec:
        # Social crawlers read the first image URL; a later fallback cannot
        # rescue a missing, relative or malformed first declaration.
        observed = page.og_image_content
        valid_first = observed is not None and _is_http_url(observed)
        matches = valid_first if spec["og_image_present"] else observed is None
        if not matches:
            fail("og_image_presence", spec["og_image_present"], observed)
    if "og_image_equals" in spec and page.og_image_content != spec["og_image_equals"]:
        # Keep URL identity exact so a changed image path or query is visible.
        # The parser already selects the first active head image declaration.
        fail("og_image_equals", spec["og_image_equals"], page.og_image_content)
    if "og_image_alt_present" in spec:
        observed = page.og_image_alt_content
        # Open Graph structured properties attach only to the preceding image
        # root. An alt tag for another image must not rescue the first image.
        matches = bool(observed) if spec["og_image_alt_present"] else observed is None
        if not matches:
            fail("og_image_alt_presence", spec["og_image_alt_present"], observed)
    if "og_image_alt_equals" in spec:
        # Guard the actual descriptive copy associated with the first image,
        # not a later image, an orphan alt, or a duplicate declaration.
        actual = (None if page.og_image_alt_content is None
                  else " ".join(page.og_image_alt_content.split()))
        if actual != " ".join(spec["og_image_alt_equals"].split()):
            fail("og_image_alt_equals", spec["og_image_alt_equals"], actual)
    for field, actual in (
        ("og_image_width_equals", page.og_image_width_content),
        ("og_image_height_equals", page.og_image_height_content),
    ):
        if field in spec:
            # Compare pixel counts numerically without parsing unbounded
            # integer strings or accepting signs, units and decimal fractions.
            matches = (actual is not None and actual.isascii() and actual.isdecimal()
                       and actual.lstrip("0") == str(spec[field]))
            if not matches:
                fail(field, spec[field], actual)
    if "og_image_type_equals" in spec:
        actual = page.og_image_type_content
        # MIME types are case-insensitive, but parameters and non-image types
        # are not valid substitutes for an explicit first-image declaration.
        if (actual is None or _IMAGE_MIME.fullmatch(actual) is None
                or actual.lower() != spec["og_image_type_equals"].lower()):
            fail("og_image_type_equals", spec["og_image_type_equals"], actual)
    if "twitter_card_equals" in spec and page.twitter_card_content != spec["twitter_card_equals"]:
        fail("twitter_card_equals", spec["twitter_card_equals"], page.twitter_card_content)
    if "twitter_title_equals" in spec:
        actual = " ".join((page.twitter_title_content or "").split())
        if actual != " ".join(spec["twitter_title_equals"].split()):
            fail("twitter_title_equals", spec["twitter_title_equals"], actual)
    if "twitter_description_equals" in spec:
        actual = " ".join((page.twitter_description_content or "").split())
        if actual != " ".join(spec["twitter_description_equals"].split()):
            fail("twitter_description_equals", spec["twitter_description_equals"], actual)
    if "twitter_image_present" in spec:
        # A valid explicit X image URL is optional; don't silently accept an
        # Open Graph fallback or let a later duplicate rescue a bad first URL.
        observed = page.twitter_image_content
        valid_first = observed is not None and _is_http_url(observed)
        matches = valid_first if spec["twitter_image_present"] else observed is None
        if not matches:
            fail("twitter_image_presence", spec["twitter_image_present"], observed)
    if "twitter_image_equals" in spec and page.twitter_image_content != spec["twitter_image_equals"]:
        fail("twitter_image_equals", spec["twitter_image_equals"], page.twitter_image_content)
    if "twitter_image_alt_present" in spec:
        # This is an explicit description guard; image URL selection may come
        # from og:image, and neither value is fetched or inferred from it.
        observed = page.twitter_image_alt_content
        matches = bool((observed or "").strip()) if spec["twitter_image_alt_present"] else observed is None
        if not matches:
            fail("twitter_image_alt_presence", spec["twitter_image_alt_present"], observed)
    if "twitter_image_alt_equals" in spec:
        observed = page.twitter_image_alt_content
        actual = None if observed is None else " ".join(observed.split())
        if actual != " ".join(spec["twitter_image_alt_equals"].split()):
            fail("twitter_image_alt_equals", spec["twitter_image_alt_equals"], actual)
    if "viewport_device_width" in spec:
        actual = _viewport_uses_device_width(page.viewport_content or "")
        if actual != spec["viewport_device_width"]:
            fail("viewport_device_width", spec["viewport_device_width"], actual)
    if "meta_refresh_present" in spec:
        actual = page.meta_refresh_contents
        if bool(actual) != spec["meta_refresh_present"]:
            fail("meta_refresh_presence", spec["meta_refresh_present"], actual)
    if "html_lang" in spec:
        actual = page.html_lang
        if actual is None or actual.lower() != spec["html_lang"].lower():
            fail("html_lang", spec["html_lang"], actual)
    if "canonical" in spec or "canonical_present" in spec:
        observed = [_resolve_canonical_href(base_url, item) for item in page.canonicals]
        if "canonical" in spec and (
            observed != [spec["canonical"]] or any(not _is_canonical_url(item) for item in observed)
        ):
            fail("canonical", [spec["canonical"]], observed)
        if "canonical_present" in spec:
            # Presence means exactly one usable canonical, not any number of
            # declarations; false means no active canonical destinations.
            valid_single = len(observed) == 1 and _is_canonical_url(observed[0])
            matches = valid_single if spec["canonical_present"] else not observed
            if not matches:
                fail("canonical_presence", spec["canonical_present"], observed)
    if "indexable" in spec or "followable" in spec:
        directives = {x.strip().lower() for content in page.robots for x in content.split(",")}
        if "indexable" in spec:
            actual = not bool({"noindex", "none"} & directives)
            if actual != spec["indexable"]:
                fail("indexability_meta", spec["indexable"], actual)
        if "followable" in spec:
            # Google combines restrictive robots/googlebot rules; an explicit
            # "follow" cannot override a separate "nofollow" or "none".
            actual = not bool({"nofollow", "none"} & directives)
            if actual != spec["followable"]:
                fail("followability_meta", spec["followable"], actual)
    for bound, condition in (("h1_min", lambda a, b: a >= b), ("h1_max", lambda a, b: a <= b)):
        if bound in spec and not condition(page.h1_count, spec[bound]):
            fail(bound, spec[bound], page.h1_count)
    if "h1_text_equals" in spec:
        actual = " ".join("".join(page.first_h1_chunks).split())
        if actual != " ".join(spec["h1_text_equals"].split()):
            fail("h1_text_equals", spec["h1_text_equals"], actual)
    if "img_missing_alt_max" in spec and page.img_missing_alt_count > spec["img_missing_alt_max"]:
        fail("img_missing_alt_max", spec["img_missing_alt_max"], page.img_missing_alt_count)
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
        result["observed_canonical"] = [
            _resolve_canonical_href(base_url, href) for href in page.canonicals
        ]
    if include_anchors:
        # Only the offline site checker interprets these relative destinations.
        result["observed_anchors"] = {"base_url": base_url, "hrefs": page.anchors}
    return result
