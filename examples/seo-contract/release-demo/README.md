# Offline HTML + sitemap release gate demo

Run this command from the Searcharis repository root (Python 3.11+ and
`uv` required). It reuses the checked-in **compiled two-page Vite HTML**
snapshot instead of building or fetching a website:

```sh
uv run python -m searcharis.release_cli examples/seo-contract/release-demo/manifest.json examples/seo-contract/generator-fixtures/vite/artifacts examples/seo-contract/release-demo/sitemap.xml --require-full-coverage --require-indexability-coverage --check-canonical-consistency --json
```

**Expected exit code: 0.** Both built pages satisfy the reviewed title,
language, canonical and indexability contracts, every built HTML page is
covered, and the supplied local sitemap lists both required URLs.

For a machine-readable CI artifact, replace `--json` with
`--json-file seo-release.json`. The target filename must not already exist
and must be outside the build tree. The command writes UTF-8 directly, does
not modify the build or sitemap and never uploads anything.

To see deliberate failures without editing this snapshot, run
`uv run pytest tests/integration/test_release_gate_demo.py -q`. Those
acceptance tests copy the compiled build and introduce a pricing-page
`noindex`, then independently omit the pricing URL from a copied sitemap.
Both cause exit 1 for the correct failing subsystem, while the other passes.
The tests also deny outbound socket connections.

The `fixtures.invalid` URLs are *reviewed example assertions*, not reachable
sites; the CLI never fetches them. This demo does not validate production
rewrites, HTTP headers, search indexing, deployment behavior or customer
adoption. For your own project, compile the site, review actual public
routes and SEO policy, commit a manifest once, supply all local sitemap
shards explicitly and run the same gate on every subsequent build.
