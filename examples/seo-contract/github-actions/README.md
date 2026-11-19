# GitHub Actions consumer example: static SEO contract CI

This **inactive, copyable example** lets an Astro/Vite web team run the existing
Searcharis offline HTML contract checker against its own compiled site in a
pull request. It is not installed into any workflow by this repository.

## Adopt in a site repository

1. Review and commit `seo-manifest.json` in the **site** repository. Start with
   a few critical pages; each entry maps a compiled HTML path beneath `dist/`
   to developer-approved SEO assertions. For a manifest shape, see
   [`../generator-fixtures/vite/manifest.json`](../generator-fixtures/vite/manifest.json).
   Do not regenerate assertions from every candidate build: that would turn
   a regression check into a self-fulfilling pass.

   For a large static build, the existing offline manifest generator can make
   a **create-only starter for just the pages you choose**. From the site
   repository root, after building `dist/` and obtaining a reviewed Searcharis
   checkout, run (replace the public base URL and paths with your own):

   ```sh
   PYTHONPATH=/path/to/searcharis/src python3 -S -m searcharis.manifest_bootstrap \
     dist https://example.com/ --only index.html --only pricing/index.html \
     --check-built --output seo-manifest.json
   ```

   The `--only` values are **exact relative built HTML paths**, not URL routes
   or glob patterns. An unknown, unsafe, or repeated path exits 2 without
   creating a manifest; a selected page failing the generated assertions exits
   1 but leaves its reviewable new manifest. Unselected HTML remains visible as
   ungated `coverage.unlisted` in the downstream CI report. Without `--only`,
   generation still includes every built HTML file and `--check-built` still
   checks full coverage. Review the generated assertions and commit them once;
   never regenerate from each candidate build during CI.

   To add another page later **without regenerating or weakening earlier
   reviews**, build the site locally and extend the previously committed
   manifest into a *new* file:

   ```sh
   PYTHONPATH=/path/to/searcharis/src python3 -S -m searcharis.manifest_bootstrap \
     dist https://example.com/ --extend-from seo-manifest.json \
     --only legal/index.html --check-built --output seo-manifest-expanded.json
   ```

   Inspect the diff, especially the new page's proposed assertions, before
   intentionally replacing the reviewed manifest in your own site repository.
   Existing contracts remain unchanged, even when a prior assertion now fails;
   with `--check-built`, that mismatch still exits 1. Duplicate page paths,
   missing old built pages, or invalid existing contracts exit 2 without
   creating output. The input manifest is never overwritten, and omitted pages
   remain unreviewed; this operation is an offline developer step, **not** a
   per-build regeneration step in CI.

   **Review the remaining pages without losing approved assertions.** After a
   local build, use the same pinned checkout to export the exact unreviewed
   built paths into a candidate text file outside `dist/`:

   ```sh
   PYTHONPATH=/path/to/searcharis/src python3 -S -m searcharis.site_cli \
     seo-manifest.json dist --unreviewed-file candidate-paths.txt
   ```

   The exported file is a **candidate queue, not an approval**. Manually inspect
   the paths and their rendered metadata, choose the pages ready for review,
   and save those exact build paths as `reviewed-next-paths.txt` (one per line;
   blank lines and `#` comments are ignored). Then generate a **new** manifest:

   ```sh
   PYTHONPATH=/path/to/searcharis/src python3 -S -m searcharis.manifest_bootstrap \
     dist https://example.com/ --extend-from seo-manifest.json \
     --only-file reviewed-next-paths.txt --check-built \
     --output seo-manifest-expanded.json
   ```

   Inspect the proposed contracts and their diff before intentionally promoting
   the expanded file to the reviewed manifest. Previously approved assertions
   remain unchanged. A missing/unsafe/duplicate selection is an input error
   (exit 2); an SEO mismatch on a selected or prior reviewed page is exit 1.
   If the candidate queue contains no unreviewed pages, there is nothing to
   import: an empty selection is rejected instead of pretending to add reviews.
   Never run queue export, manifest creation or promotion in the example CI job.
2. Copy [`seo-contract.example.yml`](seo-contract.example.yml) to the **site**
   repository's `.github/workflows/seo-contract.yml` only after its owner
   approves the workflow. Adapt the `npm ci`/`npm run build` step and the
   `seo-manifest.json` / `dist` paths to that site's actual static generator.
3. Review and intentionally advance the **full 40-character Searcharis source
   commit** in the checker checkout; never replace it with mutable `main` or a
   floating tag. All referenced Actions are also pinned to resolved commits.
   The initial Action pins were resolved against GitHub release refs on
   2026-11-19. The checker pin is the immutable merged PR #288 revision, which
   includes the unreviewed-path preview, `--unreviewed-file` queue export,
   `--only-file` curated selection and `--extend-from` incremental manifest
   updates used above. The earlier PR #285 pin lacks the queue export and
   selection-file options. Keep all manifest creation and extension separate
   from this CI gate and require deliberate human review before adoption. When
   changing the pin, verify supported generator flags and reporting behavior.
4. Check both a healthy compiled site (expected exit 0) and a deliberately
   broken canonical/title assertion (expected exit 1); missing input must exit
   2. The GitHub job must fail when either of the latter cases occurs.

The example deliberately does **not** set `--require-full-coverage` during
first-time adoption. It still fails on regressions in reviewed manifest pages,
while reporting other built HTML as `coverage.unlisted` in the JSON artifact.
The pull-request step summary lists up to ten unreviewed paths, clearly marked
**not gated**, so reviewers can select the next pages to add to the manifest.
The full list remains in the local JSON report; this example does not upload it.
After adding and reviewing all built HTML paths in the manifest, opt into
`--require-full-coverage` to make new, unreviewed pages block CI as well. Do
not mistake an incremental pass for coverage of the whole site.

## Rehearse the exact gate locally before copying

From the **Searcharis repository root**, these commands use the checked-in
compiled fixtures, Python's standard library, and the same flags as the
workflow. They require neither a web app build nor cloud credentials. On a
POSIX shell (including the Ubuntu GitHub runner):

```sh
pass_reports="$(mktemp -d)"
PYTHONPATH="$PWD/src" python3 -S -m searcharis.site_cli \
  examples/seo-contract/generator-fixtures/vite/manifest.json \
  examples/seo-contract/generator-fixtures/vite/artifacts \
  --check-canonical-consistency \
  --report-dir "$pass_reports" --github-annotations
# Expected exit 0: all reviewed fixtures match.

broken_reports="$(mktemp -d)"
PYTHONPATH="$PWD/src" python3 -S -m searcharis.site_cli \
  examples/seo-contract/regression-demo/manifest.json \
  examples/seo-contract/regression-demo/candidate \
  --check-canonical-consistency \
  --report-dir "$broken_reports" --github-annotations
# Expected exit 1: intentional SEO regressions are detected.
```

Each successful or regression run creates `seo-site.json`, `seo-site.md`,
`seo-site.xml`, and `seo-site.sarif` in its separate report directory.
The report directory **must exist before invocation** (hence `mktemp -d`
above and `mkdir -p` in the workflow). A missing manifest or unreadable
artifact exits 2 and does not create a trusted report bundle. The check
does not fetch URLs, and passing these fixture rehearsals does not prove
that another site's workflow has been installed or executed.

The checker is launched with `python -S` and `PYTHONPATH` pointed at the pinned
source checkout. This specific offline `site_cli` entry point currently runs
on the Python standard library without installing Searcharis' cloud/demo
dependencies. This is deliberate; other Searcharis services and CLIs may have
different requirements. The sample downloads the site build dependencies and
GitHub Action/source checkouts in the normal way; the **SEO verifier itself**
never fetches URLs supplied in contracts.

The job grants only `contents: read`, does not persist checkout credentials,
has no secrets, and does not deploy or upload reports. It creates JSON, JUnit,
Markdown and SARIF files within `seo-ci-reports/` and appends a bounded
Markdown reviewer summary to `GITHUB_STEP_SUMMARY` only after a checker exit
of 0 or 1. The original exit is preserved, and an exit 2 is not interpreted as
a trustworthy report. CI artifacts can contain page URLs and findings: add
optional upload/retention and permissions only with separate owner review.

This workflow checks **compiled static HTML** and explicitly reviewed routes,
not dynamic DOM, effective server headers, live robots.txt, Google indexing,
rankings or customer usage. It must not be used to claim those outcomes.

For the complete input contract and additional sitemap/robots checks, see
[`../../../docs/CI-INTEGRATION.md`](../../../docs/CI-INTEGRATION.md).
