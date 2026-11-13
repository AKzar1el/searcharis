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

For a site with multiple generated sitemap URL sets, list every local XML file
explicitly (in any order), so one reviewed manifest is checked against their
union:

```sh
uv run python -m searcharis.sitemap_cli seo-manifest.json dist/sitemap-1.xml dist/sitemap-2.xml --json
uv run python -m searcharis.sitemap_cli seo-manifest.json dist/sitemap-1.xml.gz dist/sitemap-2.xml --json
```

Local files with a `.gz` suffix are decompressed as gzip before XML validation;
plain `.xml` files and mixed compressed/plain batches work unchanged. Invalid
or truncated gzip data is an input error (exit 2). The checker does not download
or decompress remote URLs from sitemap index entries.

Missing indexable URLs and unexpected noindex URLs are evaluated across all
supplied files. Duplicated URL identities (including aliases) and conflicting
hosts across files are input errors (exit 2). This is not sitemap index
resolution: the checker never follows `<sitemapindex>` URLs, reads remote
content, or automatically discovers files. Supply and review every local URL
set yourself; an omitted shard can cause an intentional missing-URL failure.

## Optional one-command built-HTML + sitemap release gate

For CI that needs **both** the built HTML contracts and explicit sitemap
indexability checks, run one opt-in command with the same reviewed manifest:

```sh
uv run python -m searcharis.release_cli seo-manifest.json dist dist/sitemap.xml --require-full-coverage --json
uv run python -m searcharis.release_cli seo-manifest.json dist dist/sitemap-1.xml dist/sitemap-2.xml --check-canonical-consistency
uv run python -m searcharis.release_cli seo-manifest.json dist dist/sitemap.xml --require-full-coverage --json-file ci-artifacts/seo-release.json
```

`release_cli` reads the manifest once and invokes the same local verification
functions used by `site_cli` and `sitemap_cli`. The JSON report has `site` and
`sitemap` sections plus a combined `status`. Exit **0** requires both to pass;
**1** means one or both found a reviewed SEO mismatch; **2** means an input is
invalid or unreadable and no passing release claim is made. The optional HTML
flags `--require-full-coverage`, `--check-hreflang-reciprocity`,
`--check-hreflang-clusters` and `--check-canonical-consistency` keep their
existing meanings. The sitemap gate always requires at least one explicitly
reviewed `indexable` assertion, so teams with no such policy should use
`site_cli` alone until the manifest is reviewed.

Use `--json-file PATH` instead of `--json` to save the same complete combined
report directly as UTF-8 for CI consumers (including Windows PowerShell 5.1,
which can redirect stdout using UTF-16LE). The parent directory must already
exist; the target must be new and outside the static build directory, including
its resolved aliases. The report is create-only and never replaces an existing
file. Passing and mismatched reports are written with exit 0 and 1 respectively;
on invalid input or output-path errors, exit 2 is reported on stderr and no
successful release artifact is claimed. Neither the built site nor any sitemap
is modified. Do not automatically upload reports or change protected CI workflows.

This only checks files already present on disk; it does **not** build the site,
discover sitemap shards, resolve XML sitemap indexes, fetch URLs, change
deployment settings, or upload CI artifacts. Neither input is changed.
Separate `site_cli` / `sitemap_cli` invocations remain supported, including
their individual machine-readable outputs and SARIF options where available.

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

Only standard-namespace `urlset` files with `<url><loc>...</loc></url>` are
accepted. Sitemap indexes remain **unsupported**. Each file is limited to
4,000,000 uncompressed XML bytes and 50,000 URLs; `.gz` inputs also have a
4,000,000-byte compressed input cap. A batch is limited to 16 files, 16,000,000
total **uncompressed** XML bytes and 100,000 total URLs. The manifest may have
up to 10,000 pages.
The XML must be UTF-8; DTDs/entities are rejected. Sitemap URLs must be
absolute, fragment-free, and from one host across all files. Optional XML
metadata (`lastmod`, `priority`, localized extension tags) is ignored; the
check makes no attempt to prove any metadata claims.

This is a **reviewed intention check**, not a crawler or an indexing
prediction. It cannot validate live HTTP statuses, redirects, public routing,
HTTP `X-Robots-Tag`, `robots.txt`, rendered JavaScript, Search Console uptake,
Google's chosen canonicals or rankings. A pass is not proof that a URL is
discoverable or indexed. Inspect actual production routes and hosting policy
separately before enabling a release gate; do not generate or overwrite the
reviewed manifest automatically on each build.

References: [Sitemaps protocol](https://www.sitemaps.org/protocol.html),
[Google Search Central sitemap guidance](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap).
