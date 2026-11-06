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
2. Add directory/site-manifest mode with deterministic cross-page
   hreflang and canonical checks.
3. Add static site generators' artifact mapping and a documented,
   unprivileged GitHub Action.
4. Validate against independent real-world fixtures; never depend on production
   credentials or use undocumented ranking claims.
