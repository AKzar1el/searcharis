# Offline XML sitemap intent checks

Searcharis can compare a **local `sitemap.xml`** with a reviewed version-1 SEO
page manifest. This is an optional release/CI check, independent of its
Google Cloud incident demonstration. It makes no network requests and does
not run a web build, fetch a contract URL or submit to Search Console.

## Run the local example

```sh
uv run python -m searcharis.sitemap_cli examples/seo-contract/sitemap-intent/manifest.json examples/seo-contract/sitemap-intent/sitemap.xml
uv run python -m searcharis.sitemap_cli examples/seo-contract/sitemap-intent/manifest.json examples/seo-contract/sitemap-intent/sitemap-with-noindex.xml --json
```

The first command passes (exit 0); the second intentionally fails (exit 1)
because the reviewed noindex URL is included. An unreadable or invalid input
returns exit 2. `--json` emits a deterministic report on all three outcomes.
For your own build, supply your **own, reviewed** `seo-manifest.json` and
`path/to/dist/sitemap.xml`. Run `site_cli` separately on the built HTML to
check that the actual pages still satisfy their reviewed SEO contracts.

## Policy (only explicit assertions count)

- `indexable: true` requires the page's reviewed `canonical` URL in the
  sitemap when a canonical assertion is present, otherwise its `url`.
- `indexable: false` requires its own `url` to be **absent** from the sitemap.
  A different canonical target is not forbidden just because a noindex page
  points to it.
- Pages with **no** `indexable` field are unasserted and never create a
  required/forbidden sitemap URL. Unrelated sitemap URLs are not failures:
  the manifest may represent only a subset of the site. A manifest with **no**
  explicit indexability assertion is rejected (exit 2), not reported as PASS.
- URLs use the same HTTP(S) identity comparison as multi-page SEO CI
  (hostname case, default ports, unreserved percent encoding, dot segments).
  Duplicate sitemap URL identities and contradictory reviewed intentions are
  input errors rather than silent passes.

Only a single standard-namespace `urlset` with `<url><loc>...</loc></url>` is
accepted. Sitemap indexes are **not** supported in this first local check.
Input is limited to 4,000,000 bytes, 50,000 sitemap URLs and 10,000 manifest
pages. The XML must be UTF-8; DTDs/entities are rejected. Sitemap URLs must be
absolute, fragment-free, and from one host. Optional XML metadata (`lastmod`,
`priority`, localized extension tags) is ignored; the check makes no attempt
to prove any metadata claims.

This is a **reviewed intention check**, not a crawler or an indexing
prediction. It cannot validate live HTTP statuses, redirects, public routing,
HTTP `X-Robots-Tag`, `robots.txt`, rendered JavaScript, Search Console uptake,
Google's chosen canonicals or rankings. A pass is not proof that a URL is
discoverable or indexed. Inspect actual production routes and hosting policy
separately before enabling a release gate; do not generate or overwrite the
reviewed manifest automatically on each build.

References: [Sitemaps protocol](https://www.sitemaps.org/protocol.html),
[Google Search Central sitemap guidance](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap).
