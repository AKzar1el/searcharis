# Integrating the offline SEO contract checker into CI

This guide covers **built HTML only**. It is not a production crawl or an
indexing guarantee. Use the existing Searcharis hosted incident workflow
separately when an external production audit is required.

## 1. Build a known HTML artifact

For Astro, a standard static build produces files under `dist/`. Choose one
page deliberately rather than assuming a uniform path across languages or
trailing-slash configurations:

```sh
npm ci
npm run build
test -f dist/en/index.html
```

For other generators, use the corresponding output path. If your artifact
only exists after client-side JavaScript, the offline checker cannot
evaluate its rendered DOM.

## 2. Define an explicit contract

Store a JSON contract alongside each build fixture, with an absolute
public base URL. A minimal example:

```json
{
  "url": "https://example.com/en/",
  "title_present": true,
  "canonical": "https://example.com/en/",
  "indexable": true,
  "h1_min": 1,
  "h1_max": 1,
  "jsonld_types": ["WebPage"]
}
```

Only stated assertions are checked. In particular, `indexable` observes
HTML robots meta directives; it cannot inspect HTTP `X-Robots-Tag`,
robots.txt, or real search-engine indexing.

## 3. Check the artifact before deploying

From an environment with this Searcharis Python project installed:

```sh
uv run python -m searcharis.contract_cli   path/to/expected.json   path/to/dist/en/index.html   --json
```

Exit 0 means contract matched; 1 means an assertion failed; 2 means
invalid input. Keep a failing result as a failing CI result.

## 4. Bounded adoption

Start with the highest-value home, pricing and locale landing pages.
Use separate contracts per page initially. For multiple built pages, a
versioned JSON manifest provides per-page findings and explicit coverage:

```sh
uv run python -m searcharis.site_cli \
  path/to/site-manifest.json path/to/dist --json
```

The manifest declares relative HTML artifact paths under the build root and
their inline SEO contracts. Unlisted HTML is visible in the JSON report but
does not fail CI unless `--require-full-coverage` is supplied. Start with
important routes before enforcing whole-site coverage. This is a local
artifact check, not a production crawl; it does not validate cross-page
reciprocal hreflang. CI does not mutate the production site.

## 5. Release checks

- Smoke-test regression-only CI with the checked-in two-page example (from the
  repository root, Python 3.11+ and `uv` installed):

  ```sh
  uv run python -m searcharis.build_compare examples/seo-contract/regression-demo/manifest.json examples/seo-contract/regression-demo/baseline examples/seo-contract/regression-demo/candidate-passing --require-full-coverage --json
  # Expected exit 0: no new issues, one inherited home-page description issue.
  uv run python -m searcharis.build_compare examples/seo-contract/regression-demo/manifest.json examples/seo-contract/regression-demo/baseline examples/seo-contract/regression-demo/candidate --require-full-coverage --json
  # Expected exit 1: new missing pricing-page title; inherited debt remains allowed.
  ```

  CI must propagate the second command's nonzero status, rather than ignoring
  or converting it to a passing job. After smoke testing, replace the fixture
  paths with independently produced, immutable baseline/candidate build roots
  and use a developer-reviewed manifest. Neither demo verifies live URLs.
- For single-page checks, test with a known healthy HTML page and deliberately missing canonical.
- Confirm CI fails on the broken fixture rather than reporting a pass.
- Run the full repository offline unit/integration suite.
- Do not add production credentials to fixtures, logs or CI artifacts.
- Keep Google Cloud deploys manual; this guide changes no deployment workflow.

Implementation and supported fields:
[SEO-CONTRACT-CI.md](SEO-CONTRACT-CI.md).

For independently saved baseline/candidate JSON reports, see the
[archived-report Markdown review guide](ARCHIVED-REPORT-CI.md) for a bounded
human-readable CI artifact without rebuilding or fetching URLs.

For reproducible Astro-style multilingual and Vite-style static multipage
examples, see the [offline generator fixtures](../examples/seo-contract/generator-fixtures/README.md).
The checked-in snapshots are illustrative; always test your own actual build.
