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

Only fields explicitly specified in the JSON contract are checked. The
`url` is an absolute base for resolving relative canonical/hreflang URLs;
**it is never fetched**.

| Contract field | Meaning |
| --- | --- |
| `url` | Absolute HTTP(S) base URL (required) |
| `title_present` | Require a non-empty HTML title (true/false) |
| `canonical` | Require exactly one matching canonical link |
| `indexable` | Require HTML robots/googlebot meta to permit or deny indexing |
| `h1_min`, `h1_max` | Bound the number of H1 elements |
| `hreflang` | Require exactly one alternate for each named language |
| `jsonld_types` | Require Schema.org `@type` values in valid JSON-LD blocks |

## Limits and interpretation

* Validates the supplied HTML **only**; not HTTP status, X-Robots-Tag
  headers, robots.txt, redirects, JavaScript-rendered output, or actual
  search indexing.
* Checks relative URL resolution against the declared URL, not the
  build artifact's local path. Does not normalize away meaningful URL
  differences; set canonical expected values deliberately.
* Checks JSON-LD *syntax and presence of types*, not rich-result eligibility
  or all Schema.org constraints.
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
duplicate paths/URLs, invalid contracts and oversized inputs fail as input
errors. Files are limited to 4 MB each; manifests to 10,000 pages and builds
to 20,000 HTML artifacts.

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
