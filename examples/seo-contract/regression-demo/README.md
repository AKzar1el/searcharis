# Offline baseline/candidate regression demo

From the Searcharis repository root, run the local comparison directly:

```sh
uv run python -m searcharis.build_compare examples/seo-contract/regression-demo/manifest.json examples/seo-contract/regression-demo/baseline examples/seo-contract/regression-demo/candidate --require-full-coverage --sarif
```

**Expected exit status: 1 (one newly blocking regression).** The SARIF output
contains exactly one `title_presence` finding in `pricing/index.html`.
Both static builds also have the same intentional missing home-page description.
That pre-existing baseline defect is not a newly introduced regression, so it
does **not** appear in the SARIF findings.

The HTML snapshots are intentionally minimal teaching examples, **not** an
independently compiled or deployed website. Their files are stored as separate
regular files; do not replace the snapshots with symlinks or hard links.
The `seo-ci.invalid` URL values are reviewed assertions only, never requests.
The checker does not fetch URLs, build pages, verify HTTP headers or establish
actual Google indexing.

For a real release gate, supply a developer-reviewed manifest and two
independently produced, immutable local build artifacts. A trusted baseline
needs its own provenance; a successful local demo is not evidence of customer
adoption or revenue.
