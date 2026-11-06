# Static-generator artifact fixtures

These **synthetic, checked-in HTML snapshots** represent the output path
shapes of Astro's static locale routes and a Vite-served multipage static
site. They are not built by Astro or Vite in the Searcharis test suite, and
they do not prove compatibility with every generator configuration.
The `artifacts/` directories stand in for each site's `dist/` output;
the alternate name keeps fixtures versioned without changing the repository's
global `dist/` ignore rule.

From the Searcharis repository root:

```sh
uv run python -m searcharis.site_cli \
  examples/seo-contract/generator-fixtures/astro/manifest.json \
  examples/seo-contract/generator-fixtures/astro/artifacts \
  --require-full-coverage --check-hreflang-reciprocity \
  --check-hreflang-clusters --check-canonical-consistency --json

uv run python -m searcharis.site_cli \
  examples/seo-contract/generator-fixtures/vite/manifest.json \
  examples/seo-contract/generator-fixtures/vite/artifacts \
  --require-full-coverage --check-canonical-consistency --json
```

Both should pass with two declared and discovered HTML pages. The Astro
fixture additionally includes bilingual alternates, an `x-default` fallback,
robots metadata and JSON-LD. To see a regression, change a *copy* of
`astro/artifacts/sl/index.html` so its canonical points to `/en/`, then run
the checker against that copy. The exit code must be 1. To locate failures in
source files rather than the build artifact, use `--sarif` and
`--sarif-source-map examples/seo-contract/generator-fixtures/astro/source-map.json`
from the repository root.

For a real site, replace these snapshots with its actual compiled HTML,
write its own URL/route manifest, and explicitly map output files to
checked-in sources. Searcharis never runs the site generator or JavaScript,
fetches public URLs, detects response headers, or proves live indexing.
In a client-side-only Vite/React route, headings generated solely after
hydration are absent from the HTML snapshot and will correctly fail an
`h1_min` contract; use SSG/SSR or a separate rendered-DOM test.
