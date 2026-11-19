# GitHub Actions consumer example: static SEO contract CI

This **inactive, copyable example** lets an Astro/Vite web team run the existing
Searcharis offline HTML contract checker against its own compiled site in a
pull request. It is not installed into any workflow by this repository.

## Adopt in a site repository

1. Review and commit `seo-manifest.json` in the **site** repository. Each entry
   maps a compiled HTML path beneath `dist/` to developer-approved SEO
   assertions. For a manifest shape, see
   [`../generator-fixtures/vite/manifest.json`](../generator-fixtures/vite/manifest.json).
   Do not regenerate assertions from every candidate build: that would turn
   a regression check into a self-fulfilling pass.
2. Copy [`seo-contract.example.yml`](seo-contract.example.yml) to the **site**
   repository's `.github/workflows/seo-contract.yml` only after its owner
   approves the workflow. Adapt the `npm ci`/`npm run build` step and the
   `seo-manifest.json` / `dist` paths to that site's actual static generator.
3. Review and intentionally advance the **full 40-character Searcharis source
   commit** in the checker checkout; never replace it with mutable `main` or a
   floating tag. All referenced Actions are also pinned to resolved commits.
   The initial pins were resolved against GitHub release refs on 2026-11-19.
4. Check both a healthy compiled site (expected exit 0) and a deliberately
   broken canonical/title assertion (expected exit 1); missing input must exit
   2. The GitHub job must fail when either of the latter cases occurs.

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
  --require-full-coverage --check-canonical-consistency \
  --report-dir "$pass_reports" --github-annotations
# Expected exit 0: all reviewed fixtures match.

broken_reports="$(mktemp -d)"
PYTHONPATH="$PWD/src" python3 -S -m searcharis.site_cli \
  examples/seo-contract/regression-demo/manifest.json \
  examples/seo-contract/regression-demo/candidate \
  --require-full-coverage --check-canonical-consistency \
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
