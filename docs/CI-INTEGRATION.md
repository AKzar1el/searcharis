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

- Test with a known healthy HTML page and deliberately missing canonical.
- Confirm CI fails on the broken fixture rather than reporting a pass.
- Run the full repository offline unit/integration suite.
- Do not add production credentials to fixtures, logs or CI artifacts.
- Keep Google Cloud deploys manual; this guide changes no deployment workflow.

Implementation and supported fields:
[SEO-CONTRACT-CI.md](SEO-CONTRACT-CI.md).

For reproducible Astro-style multilingual and Vite-style static multipage
examples, see the [offline generator fixtures](../examples/seo-contract/generator-fixtures/README.md).
The checked-in snapshots are illustrative; always test your own actual build.
