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

For a JUnit-compatible CI test report instead, replace `--sarif` with
`--junit` in the same local command. It also exits 1 and emits one failed
`pricing/index.html :: title_presence` testcase, excluding the unchanged
home-page description failure. For a portable artifact file, use
`--junit-file seo-regressions.xml` instead of `--sarif`. The checker creates
that file without overwriting an existing report or modifying either build
snapshot, and still exits 1. Configure CI to upload the XML even on exit 1;
use a fresh output path each run.

To give a reviewer a short Markdown artifact instead, substitute
`--markdown-file seo-regressions.md` for `--sarif` above. This produces a new
UTF-8 file summarizing the pricing-page regression and one inherited baseline
issue, with fixed reviewer next steps. The expected exit remains **1**; use
a fresh output filename outside both build snapshots on each run.

The HTML snapshots are intentionally minimal teaching examples, **not** an
independently compiled or deployed website. Their files are stored as separate
regular files; do not replace the snapshots with symlinks or hard links.
The `seo-ci.invalid` URL values are reviewed assertions only, never requests.
The checker does not fetch URLs, build pages, verify HTTP headers or establish
actual Google indexing.

## Verify the passing gate before enforcing it

The separate `candidate-passing` snapshot restores the pricing title but keeps
the inherited missing home description. Run this from the repository root:

```sh
uv run python -m searcharis.build_compare examples/seo-contract/regression-demo/manifest.json examples/seo-contract/regression-demo/baseline examples/seo-contract/regression-demo/candidate-passing --require-full-coverage --json
```

**Expected exit status: 0** (`status: pass`, zero new issues, one inherited
home-page description issue). Compare it to the first command above, which
**must exit 1** for a new pricing-page title regression. Both paths use the
same reviewed manifest, no network, and independent local HTML snapshots.
This gives CI adopters a safe positive and negative gate smoke test before
substituting their own trusted baseline and candidate build directories.

For a real release gate, supply a developer-reviewed manifest and two
independently produced, immutable local build artifacts. A trusted baseline
needs its own provenance; a successful local demo is not evidence of customer
adoption or revenue.

## Optional regression-only link checks

Add `--check-reviewed-navigation` to fail only for **new** links to same-origin
routes absent from the reviewed manifest, or `--check-reviewed-fragments` to
fail only for **new** links to missing or ambiguous fragment IDs in reviewed
built pages. Both options reuse the same local HTML checks as `site_cli`; they
apply identically to baseline and candidate, so inherited broken links do not
block an otherwise unchanged release. Both checks are opt-in and can be
combined with `--json-file`, `--junit-file`, `--markdown-file` or
`--sarif-file` for CI evidence.

```sh
uv run python -m searcharis.build_compare \
  examples/seo-contract/regression-demo/manifest.json \
  examples/seo-contract/regression-demo/baseline \
  examples/seo-contract/regression-demo/candidate \
  --check-reviewed-navigation --check-reviewed-fragments
```

The command still exits 1 for this fixture's existing **new title regression**;
the fixture has no navigation or fragment regressions. Fragment checks cover
only static reviewed local HTML IDs, not JavaScript-generated elements or live
routing. Links to pages outside the reviewed manifest cannot have their
fragment targets verified. URLs in contracts and anchors are never fetched.
