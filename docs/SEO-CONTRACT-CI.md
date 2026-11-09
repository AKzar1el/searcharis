# Offline SEO contract CI (initial MVP)

Searcharis now has a second, separate verification surface: a deterministic
read-only checker for the **built HTML artifact before a release**. It does not
change or replace the existing Google Cloud incident workflow.

## Run it

```sh
uv run python -m searcharis.contract_cli \
  examples/seo-contract/expected.json \
  examples/seo-contract/page.html
uv run python -m searcharis.contract_cli \
  examples/seo-contract/expected.json \
  examples/seo-contract/page.html --json
```

Integrate into CI by running the CLI against the artifact your site's
build produces. Exit codes: **0** contract passed; **1** mismatch; **2**
invalid contract, unreadable file, or other input failure. A mismatch blocks
CI only if you configure CI to execute this checker.
With `--json`, all three exit outcomes print one JSON object to stdout:
passing/failing findings have a `status` of `pass`/`fail`, while input
errors return `{"status":"error","error":"..."}` (exit 2). Without `--json`,
input errors continue to go to stderr.

Only fields explicitly specified in the JSON contract are checked. The
`url` is an absolute base for resolving relative canonical/hreflang URLs;
**it is never fetched**.
Both the single-page contract CLI and the multi-page manifest CLI reject
duplicate JSON object keys, including nested hreflang mappings, as input
errors (exit 2) rather than silently overwriting an SEO assertion.

| Contract field | Meaning |
| --- | --- |
| `url` | Absolute HTTP(S) base URL (required) |
| `title_present` | Require a non-empty HTML title (true/false) |
| `title_equals` | Match the first active HTML head title text (opt-in, case-sensitive) |
| `description_present` | Require a non-empty first head meta description (true/false) |
| `description_equals` | Match the first active HTML head meta description content (opt-in, case-sensitive) |
| `og_title_equals` | Match the first active HTML head Open Graph `og:title` content (opt-in, case-sensitive) |
| `og_description_equals` | Match the first active HTML head Open Graph `og:description` content (opt-in, case-sensitive) |
| `og_image_present` | Require a valid absolute HTTP(S) first Open Graph image URL (`true`), or no active image declaration (`false`) |
| `og_image_equals` | Match the first active Open Graph image URL against an exact absolute HTTP(S) URL (opt-in) |
| `og_image_alt_present` | Require a non-blank `og:image:alt` for the first Open Graph image (`true`), or no alt declaration attached to that image (`false`) |
| `viewport_device_width` | Require the first head viewport meta to explicitly set `width=device-width` (true/false) |
| `html_lang` | Match the root `<html lang>` attribute (case-insensitive language tag) |
| `canonical` | Require exactly one matching canonical link |
| `canonical_present` | Require exactly one valid canonical URL (`true`) or no active canonical destination (`false`) |
| `indexable` | Require HTML robots/googlebot meta to permit or deny indexing |
| `h1_min`, `h1_max` | Bound the number of H1 elements |
| `img_missing_alt_max` | Maximum number of active HTML images without an `alt` attribute (0 requires full attribute coverage) |
| `hreflang` | Require exactly one alternate for each named language |
| `jsonld_types` | Require Schema.org `@type` values in valid JSON-LD blocks |

`jsonld_types` must name at least one non-blank type. An empty array would
otherwise silently pass even when the built page contains no JSON-LD, so it is
rejected as an invalid contract (exit 2) rather than disabling the assertion.

The declared `url` must be a syntactically valid absolute HTTP(S) URL, without
embedded whitespace, backslashes, credentials, or a malformed/out-of-range port. Both CLI
entry points reject invalid declared URLs as input errors (exit 2), including
for contracts that assert only a page title. This validates URL syntax only;
it does not fetch or check whether the URL resolves.
Expected `canonical` and `hreflang` destinations in contracts must also be
absolute HTTP(S) URLs with valid syntax. Invalid expected destinations (such as
relative paths, non-HTTP schemes, malformed ports or embedded whitespace) are
contract input errors (exit 2), not failures attributed to the built page.
HTML `<link href>` values may still be relative; they are resolved against the
document base and compared to these absolute expectations, without fetching.
Backslashes are rejected in explicit HTTP(S) URLs because browsers normalize
them differently from Python's URL parser. The same validation applies to
observed canonical targets in opt-in cross-page consistency checks, which
report invalid built links as findings rather than accepting ambiguous hosts.

## Limits and interpretation

* Validates the supplied HTML **only**; not HTTP status, X-Robots-Tag
  headers, robots.txt, redirects, JavaScript-rendered output, or actual
  search indexing.
* Resolves relative canonical and hreflang URLs against the first `<base href>`
  inside the HTML `<head>` (when present); otherwise uses the declared URL.
  An invalid or non-HTTP(S) document base fails as `document_base_invalid`.
  `<base>` inside the body or an inert template is ignored. The build artifact's
  local path is never the URL base. No URL is fetched, and meaningful URL
  differences are preserved; set canonical expected values deliberately.
* Reads titles, descriptions, canonical and hreflang links only inside the
  document `<head>`. Unlike those head-only assertions, `indexable` also
  respects active `<meta name="robots">` and `<meta name="googlebot">` in the
  `<body>`: [Google explicitly honors body robots meta](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag).
  Inert template and scripting-enabled noscript content does not contribute.
  JSON-LD scripts may appear in the head or body; visible headings are
  checked separately.
  The checker also recognizes valid HTML5 documents that omit `<html>` and/or
  `<head>` start tags (as minifiers can do). A leading HTML doctype identifies
  the implied document root; only metadata before body content is treated as
  belonging to the implied head. When body elements appear before any head
  metadata, the browser still creates an empty head: an H1-only document is
  not a structural failure merely because it omits `<head>`. Non-whitespace
  text before an omitted head starts the body and cannot turn a later `<title>`
  or `<link>` element into head metadata. Non-whitespace text inside an already
  opened head also starts the body, so a later `<link>` cannot supply a false
  canonical. Text inside
  head `<script>`, `<style>` or `<title>` does not prematurely close the head.
  ASCII whitespace before the omitted head remains permissible. See the
  [HTML optional-tag rules](https://html.spec.whatwg.org/multipage/syntax.html#optional-tags).
  `title_present` examines the first active head `<title>` only; a later
  duplicate cannot rescue a blank first title. HTML permits only one title
  element per document; this checker does not separately enforce uniqueness.
  `title_equals` additionally detects accidental title copy changes even when
  `title_present` still passes. It compares the first active head title against
  a required non-blank string, after decoding HTML entities and collapsing
  whitespace runs on both sides; letter case and punctuation remain significant.
  Body and inert template titles cannot satisfy it. This is an optional exact
  publishing contract, not a promise about Google's displayed search title.
  The text inside any `<title>` follows HTML RCDATA rules: literal tag-looking
  examples do not create active H1 or JSON-LD elements, and an example
  `<script>` does not hide real page metadata or headings after `</title>`.
  `description_present` checks the first active `<meta name="description">`
  in the head. Missing or whitespace-only `content` counts as absent; a later
  duplicate cannot rescue a blank first description. Body/template metadata
  is ignored. This optional agency policy does not mean Google requires a
  meta description or guarantees that it uses one as the search snippet.
  `description_equals` additionally guards against unexpected description copy
  changes in built pages. It requires non-blank expected text and compares the
  first active head description after decoding HTML entities and collapsing
  whitespace runs on both sides; letter case and punctuation stay significant.
  Body or template metadata cannot satisfy it, and later duplicate descriptions
  cannot override the first. This opt-in exact publishing contract does not
  predict Google's chosen search snippet.
  `og_title_equals` separately protects the title supplied for social/link
  previews through `<meta property="og:title" content="...">`. It checks the
  first active tag in the head, decoding entities and collapsing whitespace
  runs before a case-sensitive comparison to non-blank expected text. Missing,
  blank or changed content fails; inert template, body-only and `name="og:title"`
  metadata cannot rescue it. A later duplicate cannot override the first.
  This checks the built HTML, not whether a social platform actually uses or
  caches that preview title.
  `og_description_equals` similarly guards the social-preview summary in
  `<meta property="og:description" content="...">`, independently of the
  search meta description. It checks the first active tag in the head;
  entities are decoded and whitespace runs collapsed before a case-sensitive
  comparison to non-blank expected text. Missing, blank or changed content
  fails. Inert template/noscript markup, body metadata and `name="og:description"`
  cannot satisfy the assertion, and later duplicates cannot rescue the first.
  This does not test which summary a social platform displays or caches.
  `og_image_present` is an opt-in social-sharing image guard: `true`
  requires the first active head `<meta property="og:image" content="...">`
  (or the equivalent `og:image:url`) to contain a syntactically valid
  absolute HTTP(S) URL. A missing, blank, relative, protocol-relative,
  malformed or credential-bearing value fails; a later image tag cannot
  rescue a bad first one. Multiple images are valid Open Graph arrays, so
  later entries do not cause an otherwise valid first image to fail.
  `false` asserts that no active head image declaration exists, even an
  empty one. Body, inert template, noscript, `name="og:image"`, and
  `og:image:secure_url` alone cannot satisfy `true`. The checker only
  validates the declared URL syntax, without fetching the image, testing
  its existence/dimensions, or promising a platform will display it.
  See the [Open Graph protocol](https://ogp.me/) for first-tag precedence
  and optional image arrays.
  `og_image_equals` additionally catches unexpected social-preview image
  URL changes even when a valid image is present. The expected URL must be
  absolute HTTP(S). It compares the first active head `og:image` or
  `og:image:url` declaration after HTML entity decoding and surrounding
  whitespace trimming; query strings, path and case remain significant.
  An absent, relative or changed first image fails, even if later image
  declarations match. This is an exact publishing contract only: neither
  image reachability nor the preview displayed by a platform is checked.
  `og_image_alt_present` independently checks descriptive alternative text for
  the **first** Open Graph image. `true` requires non-blank content in the first
  active head `<meta property="og:image:alt">` associated with that image;
  `false` requires no associated alt declaration, even an empty one. An alt
  property appearing before any image root is orphaned; an alt after a second
  `og:image`/`og:image:url` belongs to that later image and cannot rescue the
  first. Another root property such as `og:title` or `og:video` also ends the
  first image's structured-property group, so an alt declared afterward cannot
  rescue it. Ordinary non-Open-Graph HTML metadata does not end that group.
  An empty first alt cannot be rescued by later duplicates. Inert
  templates, noscript, `name="og:image:alt"`, and body metadata are ignored.
  This is an opt-in publishing quality guard, not a test of alt-text accuracy,
  actual screen-reader behavior or social platform rendering. Combine it with
  `og_image_present` to require a valid first image URL as well. See the
  [Open Graph protocol](https://ogp.me/) structured properties and arrays rules.
  `viewport_device_width` is an optional mobile layout guard. `true` requires
  the first active `<meta name="viewport">` in the head to include exactly one
  comma-separated `width=device-width` setting (case-insensitive, spaces around
  `=` allowed); missing, fixed-width, malformed or duplicate width settings
  fail. Head templates, `<noscript>` and body metadata cannot satisfy it; a
  later viewport tag cannot rescue an incorrect first one. `false` asserts
  that this explicit setting is absent. This catches a common mobile layout
  configuration regression, not computed responsiveness, accessibility, or a
  search ranking outcome. See [MDN viewport guidance](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/meta/name/viewport).
  `html_lang` is an optional multilingual publishing assertion against the
  root `<html lang="...">` attribute. Language-tag casing is ignored, but
  region subtags are not interchangeable (`en` and `en-US` differ). A missing
  or blank root attribute fails; later `<html>` tags and template content do
  not supply a fallback. This checks an explicit authoring policy, not the
  page's actual language, a complete BCP 47 validity check, or a Google
  ranking/indexing signal. Expected tags must be non-empty without whitespace.
* `img_missing_alt_max` is an optional non-negative threshold for `<img>`
  elements that omit the `alt` attribute. For example, `0` enforces attribute
  coverage on every active image, while `2` allows up to two omissions in a
  legacy build. Empty `alt=""` attributes count as present (legitimate for
  decorative images); `<picture><source>` does not count as an image.
  Images in inert templates, scripting-enabled `<noscript>`, or text-only
  examples are excluded. The checker neither fetches image URLs nor evaluates
  alternative-text quality, accessibility conformance, or search ranking.
* When generated HTML repeats an attribute on one tag, uses its **first**
  value and ignores later duplicates, as required by the
  [HTML parsing standard](https://html.spec.whatwg.org/multipage/parsing.html#parse-error-duplicate-attribute).
  This avoids passing SEO assertions using an attribute browsers discard;
  it is not a general HTML validity checker.
* `canonical_present` is an optional guard for CI pipelines that cannot know
  the intended canonical URL ahead of time. `true` requires **exactly one**
  active `<link rel="canonical" href="...">` in the head, whose non-empty
  destination resolves to a syntactically valid HTTP(S) URL. Missing, blank,
  malformed, non-HTTP(S), and duplicate canonical destinations fail, with
  the observed resolved URLs included in the finding. `false` requires no
  active canonical destinations. Body and inert-template links do not count;
  the document's first head `<base href>` is honored without fetching it.
  This is only a presence and URL-syntax assertion: it does **not** prove
  that a canonical is self-referential, points to indexable content, or is
  accepted by Google. Use `canonical` for an expected exact target or the
  opt-in multi-page canonical-consistency check to verify known local targets.
* A blank or whitespace-only `<link href>` cannot satisfy an explicit canonical
  or hreflang URL assertion merely by resolving to the document URL. Optional
  cross-page hreflang checks also fail on blank destinations even without a
  per-page hreflang contract. The HTML standard requires a
  [non-empty link URL](https://html.spec.whatwg.org/dev/semantics.html#the-link-element).
* Malformed canonical or hreflang destinations (for example, invalid IPv6 URL
  syntax) are assertion failures rather than input/manifest errors. Explicit
  URL assertions also reject matching non-HTTP(S) destinations. Opt-in
  cross-page hreflang checks flag non-HTTP(S), malformed, or otherwise invalid
  destinations as `hreflang_invalid_url:<language>` and exclude them from the
  *unverified external target* list. Valid outside-manifest HTTP(S) targets
  remain unverified, never fetched. Default manifest checks still validate
  only the properties explicitly asserted by each page's contract.
* Checks strict JSON-LD *syntax and presence of types*, including nested entities
  such as an Article's author or a FAQPage's questions and answers, as well
  as `@graph`. Term/type declarations inside `@context` and typed literal
  values are not counted as entities. JSON-LD `@value` objects, including
  `@type: "@json"` literals containing nested JSON with `@type` keys, are
  never traversed as entity nodes. Non-JSON numeric literals (`NaN`,
  `Infinity`, `-Infinity`) fail syntax validation rather than silently passing
  via Python's permissive JSON parser. Duplicate JSON object keys, including
  nested JSON-LD keys, also fail validation rather than silently letting the
  last duplicate value satisfy an assertion. Exponents that overflow the
  checker's floating-point range (for example `1e9999`) also fail closed:
  Python would otherwise decode them as infinity, allowing an unsupported
  non-finite value to pass an asserted type. This does not verify rich-result
  eligibility or all Schema.org constraints.
  JSON-LD is limited to 256 nested object/array levels, independent of the
  Python interpreter's recursion tolerance. Excessively nested input fails
  as an invalid JSON-LD finding instead of crashing the checker.
* Excludes content inside HTML `<template>` elements from active page SEO
  assertions. A title, canonical, hreflang link, robots meta, JSON-LD block or
  H1 appearing only in an inert template cannot satisfy the contract. This is
  static HTML inspection, not JavaScript rendering or shadow-DOM evaluation.
  Inside templates, literal `</template>` text in `<title>`, `<textarea>`,
  `<xmp>`, `<iframe>`, `<noembed>` or `<noframes>` does not prematurely end the
  inert content and expose ghost headings, metadata or structured data.
* Treats HTML `<textarea>` contents as editable text rather than active markup.
  Heading and JSON-LD examples written inside a textarea cannot satisfy the
  real page's assertions; headings after the closing textarea remain visible.
  Likewise, markup text inside `<iframe>`, `<xmp>`, `<noembed>` and `<noframes>`
  is not counted as active headings or JSON-LD. Real headings after these
  elements are still checked. This reflects HTML raw-text parsing, not a
  browser rendering or JavaScript execution test.
* Counts parsed `<h1>` elements within `<select>` as well as outside it.
  The [current HTML parsing standard](https://html.spec.whatwg.org/multipage/parsing.html#parsing-main-inbody)
  uses in-body rules for customizable selects, rather than the older insertion mode that discarded unexpected
  markup. The checker counts static DOM headings, not CSS visibility or
  whether browsers render them in a native picker. A self-closing `<select/>`
  slash does not end the element.
* Parses `<noscript>` content using the **scripting-enabled** HTML tokenizer:
  headings, canonical/hreflang links, robots metadata and JSON-LD markup
  inside it cannot satisfy active-page assertions. Content after the matching
  `</noscript>` is checked normally. Browsers with scripting disabled parse
  `<noscript>` differently; Searcharis does not emulate both modes or execute
  JavaScript.
* Treats everything after the obsolete HTML `<plaintext>` start tag as text,
  including apparent closing tags. Markup-looking headings and JSON-LD after
  it cannot satisfy SEO assertions; earlier genuine headings still count.
  This follows the HTML tokenizer's plaintext state, not a rendering test.
* A trailing `/>` does not close state-bearing non-void HTML elements such as
  `<template/>`, `<textarea/>`, `<title/>`, or `<script/>`. Their content remains
  inert or text-only until the matching end tag, rather than falsely satisfying
  H1/JSON-LD assertions. Head metadata after `<head/>` is still in the head;
  ordinary void tags such as `<link ... />` retain their normal behavior. Such
  non-void trailing slashes are HTML parse errors, not a recommended syntax.
* Hreflang checks validate declared entries, not complete reciprocals
  across an entire multilingual site.
* Do not treat a passing check as proof of production SEO health.

## Follow-up directions for the coding worker

1. Improve contract fixtures, error output and test coverage before broadening
   the contract schema.
2. Extend the existing opt-in cross-page hreflang and canonical checks with
   complete locale-cluster diagnostics once justified by real fixtures.
3. Add static site generators' artifact mapping and a documented,
   unprivileged GitHub Action.
4. Validate against independent real-world fixtures; never depend on production
   credentials or use undocumented ranking claims.

## Multi-page static build manifest (offline)

Use a versioned manifest to check several built HTML pages in one CI command:

```sh
uv run python -m searcharis.site_cli \
  examples/seo-contract/site-manifest.json examples/seo-contract --json
```

The manifest format is a JSON object with `"version": 1` and a non-empty
`"pages"` array. Every page has a **relative POSIX** `"path"` (such as
`"en/index.html"`) and an inline `"contract"` using the existing fields.
The supplied build directory is the only source of HTML; contract `url`
values are never fetched. Paths escaping the build root, missing files,
paths whose casing differs from the built HTML (including on Windows),
duplicate paths/URLs, invalid contracts and oversized inputs fail as input
errors. Duplicate JSON object keys (including inside page contracts) are
rejected rather than silently overriding SEO assertions. Files are limited to
4 MB each; manifests to 10,000 pages, and builds to 20,000 HTML artifacts.
On case-sensitive builds, a second artifact differing only by filename case
is still a distinct unlisted file; `--require-full-coverage` blocks it rather
than counting one manifest entry as coverage for both artifacts. To obtain
full coverage, list both exact-cased artifact paths with distinct contract URLs.
The checker rejects exact duplicates and wrong-cased paths on Windows too;
merely using a case-insensitive filesystem never silently proves coverage.

The result includes per-page findings and a coverage record listing any built
HTML not declared in the manifest. Partial adoption is supported: unlisted
pages are **reported but not failures** by default. For complete-site CI,
add `--require-full-coverage` to fail on any unlisted HTML.

Exit codes: **0** all assertions passed (and coverage if enforced),
**1** assertion or enforced-coverage failure, **2** invalid input. Add
`--json` for deterministic machine-readable findings, including JSON
errors with exit 2. This mode checks each declared page independently by
default. Cross-page canonical relationships and reciprocal hreflang links
require explicit flags below. It does not change deployment or run a crawl.

## Reciprocal hreflang between local build artifacts

For multilingual builds, opt into checking that HTML language alternates
between the URLs **present in the manifest** have matching return links:

```sh
uv run python -m searcharis.site_cli \
  examples/seo-contract/bilingual-manifest.json \
  examples/seo-contract/bilingual-build \
  --check-hreflang-reciprocity --require-full-coverage --json
```

The checker reads the actual HTML alternate links, even if the corresponding
per-page contracts do not individually assert `hreflang`. A page advertising
language alternatives must include a language-tagged link to itself. If page
A links to a page B whose URL is listed in the manifest, B must link back
to A exactly once for every language label A assigns to itself. Failures
include the source path, target URL and observed return links. Language labels
are case-insensitive; `x-default` is a fallback, not a language identity.
The check is opt-in so existing CI contracts and exit behavior are unchanged.

An alternate pointing to a URL **absent from the manifest** is reported in
`hreflang_reciprocity.unverified_targets`, but does not fail: the checker
cannot prove the destination has the return link. This remains true with
`--require-full-coverage`, which applies only to built HTML file paths.
For complete reciprocal coverage, provide local artifacts and manifest entries
for every language variant, including cross-domain variants if applicable.
The checker does not fetch missing URLs, parse HTTP Link headers or XML
sitemaps, verify language correctness, or prove Google will use the annotations.
This scope follows the bidirectional HTML-link guidance in
[Google Search Central](https://developers.google.com/search/docs/specialty/international/localized-versions).

## Cross-page canonical consistency in local builds

Opt into checking canonical signals in actual built HTML, even when individual
page contracts do not assert canonical URLs:

```sh
uv run python -m searcharis.site_cli \
  examples/seo-contract/bilingual-manifest.json \
  examples/seo-contract/bilingual-build \
  --check-canonical-consistency --require-full-coverage --json
```

Each manifest page must have **exactly one** HTML canonical link resolving to
an HTTP(S) URL. Self-canonical pages pass. A page may point to another page,
including a deliberate duplicate, but when that target URL is also in the
manifest its built HTML must contain exactly one canonical to **itself**.
This detects canonical chains, cycles, missing links on known targets and
duplicate canonical declarations. Findings use
`canonical_consistency.internal_targets_checked` and
`canonical_consistency.unverified_targets` (with source artifact paths).

Targets **absent from the manifest** are reported as unverified, not counted as
checked or treated as verified passes. That includes cross-domain canonicals.
Include target HTML and its URL in the manifest to verify it offline.
`--require-full-coverage` governs local HTML only, not remote target URLs.
The checker never fetches canonical URLs. It does not inspect HTTP Link
headers, redirects, robots directives, sitemap hints, JavaScript DOM,
Google's selected canonical, or live indexing. This is an opt-in strict CI
policy, not a claim that search engines require a self-canonical tag on
every page. Both optional site-level checks can be combined, and the default
manifest exit behavior remains unchanged.

## Strict hreflang locale-cluster consistency (optional)

For sites intending to publish the same *complete* locale map on every page in
one language cluster, opt into an additional offline policy:

```sh
uv run python -m searcharis.site_cli \
  examples/seo-contract/bilingual-manifest.json \
  examples/seo-contract/bilingual-build \
  --check-hreflang-reciprocity --check-hreflang-clusters --json
```

`--check-hreflang-clusters` compares each linked pair of manifest-listed HTML
pages. For each locale label, including `x-default`, both pages must specify
the same single manifest-listed target URL. Missing local variants, conflicting
local destinations and duplicate locale labels fail this strict policy. The
report lists directed `checked_pairs` and `unverified_targets` with path,
language and URL. If either side of a comparison mentions an outside-manifest
URL for a locale, that locale is *not* considered verified; it is not declared
missing either. Add all relevant local HTML artifacts to the manifest to
check the complete set, including cross-domain pages when available locally.

This is an **optional team consistency rule**, not a claim that Google requires
complete hreflang sets. Sites intentionally using partial reciprocal language
links can use `--check-hreflang-reciprocity` alone. Neither flag checks whether
the language codes match the content, fetches any URLs or verifies live indexing.

## Compare two local JSON reports without blocking existing defects

Teams adopting contracts on an older website can gate **new** regressions
without temporarily failing on known pre-existing issues. Run the site checker
with identical manifest and flag policies on the trusted baseline build and
on the candidate build, saving their complete `--json` outputs:

```sh
uv run python -m searcharis.site_cli manifest.json baseline-build --json > baseline.json
uv run python -m searcharis.site_cli manifest.json candidate-build --json > candidate.json
uv run python -m searcharis.report_compare baseline.json candidate.json --json
```

The first two invocations may return **1** when their build has known contract
failures; when collecting reports, handle that exit status without aborting
the comparison, but always stop on **2** (invalid input). The last invocation
returns **0** if no new regressions are found, **1** for new or changed page
issues, missing baseline routes, or newly unlisted HTML when full coverage
is enabled, and **2** for unreadable, malformed or incompatible reports.

Comparison uses the exact artifact path, declared URL and full issue details;
changing an observed value counts as a new failure, while a persistent issue
does not. Repeated identical findings are counted individually: a second
invalid hreflang link cannot hide behind an existing identical failure.
On case-sensitive builds, case-distinct HTML artifact paths remain separate
whether declared or unlisted in comparison reports. A newly unlisted case
variant is a regression under full coverage, not an invalid report. Explicit
case-distinct pages can each have their own findings and contract fingerprint;
exact duplicate paths and duplicate contract URLs are still invalid.
A route present in the baseline but missing from the candidate fails
closed, even if that route had no issues. Intentional route removals need an
approved baseline update. Existing and resolved issues are still reported.
New outside-manifest canonical/hreflang targets are surfaced as **unverified**,
never treated as successfully checked or automatically blocking.

Use a **trusted, review-controlled baseline from the protected branch**.
Reports must use matching full-coverage and optional cross-page check flags,
and each local report is limited to 16 MB. Neither report is downloaded by
Searcharis: both files must already be present locally. This tool reads
no live URLs or production credentials, and does not create a GitHub
workflow, decide whether a baseline is trustworthy or prove real search
indexing health.
Each newly generated page report also includes a deterministic SHA-256 of its
declared SEO contract (not the HTML). The comparator rejects different
assertions for the same artifact between baseline and candidate, instead of
silently passing when a candidate drops a check. JSON property order does not
change the fingerprint; adding new routes remains supported. Two older reports
without fingerprints can still be compared, but cannot prove their assertions
were identical. Mixing fingerprinted and legacy reports fails as incompatible:
regenerate both reports with the same checker version and approved manifest.
Non-finite numeric values in report data (such as `NaN`, `Infinity`, or a
number overflowing to infinity during JSON decoding) and excessively nested
JSON reports are invalid input (exit 2), not a successful no-regression
comparison or an uncaught traceback.

## Optional SARIF findings for artifact-level CI reporting

Export the same offline site checks as SARIF 2.1.0 rather than JSON:

```sh
uv run python -m searcharis.site_cli \
  examples/seo-contract/bilingual-manifest.json \
  examples/seo-contract/bilingual-build \
  --check-canonical-consistency --require-full-coverage --sarif
```

`--sarif` and `--json` are mutually exclusive. SARIF is printed to stdout;
capture it as **UTF-8** when writing a `.sarif` file for a CI consumer.
The usual process exit codes are unchanged: 0 for pass, 1 for failed checks,
2 for invalid input. An input error is printed to stderr, **not** as a
misleading valid SARIF run. When capturing a report in a shell configured to
abort on exit 1, explicitly retain its output and treat 2 as an input failure.

Each failed page assertion becomes an error result; if
`--require-full-coverage` is used, unlisted built HTML is also an error.
The format groups locale-specific check codes under stable rule IDs, includes
deterministic issue fingerprints and percent-encodes relative artifact paths.
Repeated identical findings in one built artifact receive distinct,
deterministic occurrence-based partial fingerprints, so separate failures do
not share a fingerprint. The first occurrence keeps its prior fingerprint.
SARIF consumers can apply their own additional matching rules; this does not
guarantee a separate alert for every occurrence in every consumer.
Ordinary non-strict unlisted pages and **unverified** off-manifest canonical
or hreflang targets do not become verified/error findings.

By default, SARIF locations refer to generated HTML paths **relative to the
supplied build root** (SARIF base ID `%BUILDROOT%`). To annotate checked-in
templates instead, supply an **explicit** artifact-to-source map. For example,
from the repository root of a built Astro site:

```sh
uv run python -m searcharis.site_cli manifest.json dist \
  --sarif --sarif-source-map source-map.json
```

```json
{
  "version": 1,
  "sources": {
    "en/index.html": "src/pages/en/index.astro",
    "sl/index.html": "src/pages/sl/index.astro"
  }
}
```

For Vite projects the `sources` values can instead refer to checked-in
`index.html`, JSX, TSX or other source files, as appropriate for the build.
Paths on the left are **relative to `dist`** and must refer to discovered
HTML artifacts. Paths on the right are **relative to the current working
directory**, which must be the source repository root. Each right-hand
source file must exist under that root; traversal, broken links, symlink escapes,
duplicate JSON keys, unknown artifact paths, unsupported versions and maps
larger than 1 MB fail with exit code 2. Maps can cover only a subset of the
build; unmapped findings continue to point to generated HTML. Multiple
artifacts may map to the same source template for statically generated routes.
Artifact paths must match the build **exactly**, including case. On a
case-sensitive build, both `en/index.html` and `en/INDEX.HTML` may be mapped
independently; a wrong-cased name absent from the build remains invalid.

Mapped SARIF findings use the repository-relative source path as their primary
location and preserve the original built HTML in `relatedLocations`; the
issue fingerprint remains based on the built artifact and finding. **Line 1 is
only a document-level anchor**, not the precise offending source line. Mapping
is user-declared, not inferred or verified from source maps, and does not prove
that the selected template produced the artifact. A GitHub code-scanning
consumer still needs the source paths in its checkout. No GitHub workflow,
SARIF upload, network fetch or deployment is performed by Searcharis.

## Static generator adoption fixtures (Astro and Vite)

The [generator fixtures](../examples/seo-contract/generator-fixtures/README.md)
exercise the full CLI with bilingual Astro-style output and a Vite-style
static multipage layout. They cover declared routes, strict HTML coverage,
canonical/locale relationships, regression failures, and SARIF mappings to
explicitly named Astro templates. Unit and integration tests enforce the
expected exit statuses, including a failure when an HTML heading would exist
only after JavaScript hydration.

The Astro pages are synthetic snapshots with the relative path layout of
common `dist/` directories; they have **not** been compiled with Astro.
The Vite multipage pages and their hashed JS asset were built from checked-in
fixture sources with Vite 5.4.21 and are verified by static integration tests.
Searcharis tests do not install or invoke either compiler. Integrators must
supply their own compiled output and route contracts; dynamic SPA content
is outside the offline parser's scope.
