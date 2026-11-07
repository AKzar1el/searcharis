# Static-generator artifact fixtures

These checked-in HTML snapshots represent Astro's static locale routes and
a Vite multipage static site. The **Astro snapshots are synthetic**; the
**Vite snapshots are compiled** from the checked-in `vite/site/` sources
using Vite 5.4.21 on Node.js 22.22.2. Tests do not execute Astro, Vite, or
client-side JavaScript, and these fixtures do not prove compatibility with
every generator configuration.
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

The Vite sample was built locally, offline, using two HTML entry points
(`vite/site/index.html` and `vite/site/pricing/index.html`) and the shared
`vite/site/main.js` import. The compiled HTML references the actual hashed
bundle in `vite/artifacts/assets/`. The integration tests verify this
source-to-output transformation, the artifact's presence, both SEO contracts,
and missing server-rendered headings. No package manifest, lockfile, workflow,
or dependency was added to Searcharis.

To reproduce the Vite output with the pinned compiler, create a *temporary*
Vite config outside `vite/site/` containing the following (run from the
Searcharis repository root):

```js
import { resolve } from "node:path";
const root = resolve(process.cwd(), "examples/seo-contract/generator-fixtures/vite/site");
export default {
  root,
  build: {
    outDir: resolve(process.cwd(), ".revenue-shot/vite-rebuild-output"),
    emptyOutDir: false,
    rollupOptions: {
      input: {
        home: resolve(root, "index.html"),
        pricing: resolve(root, "pricing/index.html"),
      },
    },
  },
};
```

Run `npm exec --offline --yes --package=vite@5.4.21 -- vite build --config <temporary-config-path>`
with the pinned package already present in the local npm cache. Review the
output before updating checked-in artifacts. The command never targets the
published site, and normal Python CI does not require Node.js.

For a real site, replace these snapshots with its own compiled HTML,
write its own URL/route manifest, and explicitly map output files to
checked-in sources. Searcharis never runs the site generator or JavaScript,
fetches public URLs, detects response headers, or proves live indexing.
In a client-side-only Vite/React route, headings generated solely after
hydration are absent from the HTML snapshot and will correctly fail an
`h1_min` contract; use SSG/SSR or a separate rendered-DOM test.
