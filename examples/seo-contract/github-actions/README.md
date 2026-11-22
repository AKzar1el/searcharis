# GitHub Actions consumer example: static SEO contract CI

This **inactive, copyable example** lets an Astro/Vite web team run the existing
Searcharis offline HTML contract checker against its own compiled site in a
pull request. It is not installed into any workflow by this repository.

## Smaller integration: the standalone offline site gate

For a team that already maintains and reviews `seo-manifest.json`, the inert
[`seo-site-action/action.yml`](seo-site-action/action.yml) packages **only the
compiled-site SEO check** as a reusable composite action. A consumer can call it
from an Ubuntu pull-request workflow after building its own static HTML:

```yaml
on:
  pull_request:
permissions:
  contents: read
jobs:
  offline-seo:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          persist-credentials: false
      - uses: actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1
        with:
          node-version: '22'
      - uses: actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0
        with:
          python-version: '3.13'
      - run: npm ci && npm run build  # Adapt to your static generator.
      # - run: mkdir -p seo-ci-reports  # Only when retaining local reports.
      - uses: AKzar1el/searcharis/examples/seo-contract/github-actions/seo-site-action@edff0f6415e423103a6bc7647f5c3e2f75798467
        with:
          manifest: seo-manifest.json
          build-root: dist
          # Only with a separately prepared, trusted baseline, not PR content:
          # baseline-build-root: trusted-baseline-dist
          # With that trusted baseline, opt in to only *new* coverage gaps:
          # fail-on-new-unlisted: 'true'
          # fail-on-new-unverified-targets: 'true'
          require-full-coverage: 'false'
          # To retain source-linked SARIF, enable both options after reviewing
          # and committing seo-source-map.json and creating seo-ci-reports/:
          # report-directory: seo-ci-reports
          # sarif-source-map: seo-source-map.json
          # For multilingual sites, opt in to local hreflang return-link checks:
          # check-hreflang-reciprocity: 'true'
          # Only for complete language sets, check consistent locale clusters:
          # check-hreflang-clusters: 'true'
          # After reviewing intentional metadata reuse, opt in independently:
          # check-unique-titles: 'true'
          # check-unique-descriptions: 'true'
          # After reviewing internal link policies, opt in separately:
          # check-internal-nofollow: 'true'
          # Only with a complete reviewed route list, check navigation targets:
          # check-reviewed-navigation: 'true'
          # Independently check static anchors on reviewed local HTML:
          # check-reviewed-fragments: 'true'
          # After reviewing important pages and their inbound links:
          # require-inbound-paths: |
          #   pricing/index.html
          #   services/index.html
```

The short recipe pins the same Node 22 build runtime as the fuller
[`seo-contract.example.yml`](seo-contract.example.yml) instead of depending on
the runner image's changing default Node version. Adjust the pinned runtime to
the consumer site's supported Node version, retain immutable Action references,
and ensure the site has a reviewed `package-lock.json` for `npm ci`.

The example pins the **independently merged PR #337** main revision
`edff0f6415e423103a6bc7647f5c3e2f75798467`, which contains the optional
report-directory, hreflang reciprocity, duplicate-metadata, internal-nofollow,
reviewed-navigation, reviewed-fragments, strict locale-cluster, and critical
inbound-path inputs, reviewed SARIF source hints, and regression-only baseline
comparison including incremental coverage checks. It rejects explicitly blank
optional boolean inputs (exit 2), bounds SARIF source-map reads against the
actual input stream before JSON parsing (merged #334), and includes later
offline sitemap and archived-report correctness fixes (#335-#337). Earlier
pinned revisions may silently ignore unrecognized inputs or lack these fixes. Review that source
and the website's own workflow before adoption; nothing is installed
automatically. Advance the pin only to a
newer, independently reviewed, full 40-character merged commit. Do not pin
`main`, a floating tag or an unmerged branch.
The action needs Python 3.11+ and Bash, but neither downloads dependencies nor
contacts a live URL or cloud service. It uses the source bundled with the pinned
action, reads only supplied local HTML/JSON and appends a bounded GitHub step
summary on exit **0** (pass) or **1** (policy regression). Invalid/missing input
exits **2** without a misleading success summary. By default, unreviewed HTML
remains reported but not blocked; set `require-full-coverage: 'true'` only after
all pages are deliberately reviewed.

### Optional regression-only comparison against a trusted build

The composite Action source also accepts a default-empty
`baseline-build-root` input. When supplied, the Action delegates to the
existing offline `searcharis.build_compare` instead of the strict
`searcharis.site_cli`: **existing baseline failures do not block**, but newly
introduced SEO regressions fail with exit **1**. Invalid or overlapping build
roots fail with exit **2** without a success summary. The same manually
reviewed manifest and opt-in checks apply to both local builds. The Action
does **not** fetch, generate, deploy or authenticate the baseline.

Prepare a **trusted, independently reviewed** baseline build in a distinct
directory before invoking the Action; do not reuse a pull-request-controlled
build as its own baseline or alias it through shared HTML hard links. When
retaining reports, the Action writes `seo-comparison.{json,xml,md,sarif}`
create-only into the existing `report-directory` and appends the Markdown
comparison to the GitHub step summary only on exits 0 or 1. Manifest approval
against the immutable trusted PR base remains a **separate mandatory gate**:
regression-only results cannot authorize new or weakened manifest assertions.

**Pin compatibility:** The compact copyable `uses:` example above pins the
independently merged #337 main revision and already supports the regression-only input.
The commented `baseline-build-root` is **not enabled by default**. Its example
directory name is a placeholder: separately prepare and review a real baseline
outside untrusted pull-request content before enabling it. Older pins may ignore
unknown inputs without failing. No customer installation, ranking improvement
or revenue is asserted by local tests.

### Incremental coverage for gradually reviewed sites

For a trusted baseline comparison, two independent, default-off Action inputs
expose existing offline `build_compare` checks:
`fail-on-new-unlisted: 'true'` rejects newly created HTML outside the reviewed
manifest while tolerating *inherited* unlisted pages. Separately,
`fail-on-new-unverified-targets: 'true'` rejects newly unverified canonical or
hreflang destinations from enabled cross-page checks (the Action already enables
canonical consistency). These checks do not approve any new manifest entries,
fetch URLs, or claim that targets are broken on the live site. Invalid boolean
values (including explicitly empty strings), or enabling either without
`baseline-build-root`, fail as input error 2.
Both flags stay false for strict single-build checks and for comparisons unless
the operator deliberately opts in.

**Compatibility:** The copyable `uses:` reference above already supports both incremental coverage inputs
through the independently merged #337 main revision.
Both inputs are shown as comments and remain disabled by default; do not enable
either without a separately prepared trusted baseline. Keep that baseline
independent of pull-request-controlled content and run the separate
approved-manifest comparison against the immutable PR base.

For multilingual static sites, set `check-hreflang-reciprocity: 'true'`
under the action step's `with:` inputs. The default `'false'` preserves
existing runs. This optional **offline** check requires a reciprocal
`hreflang` link between each pair of language pages represented in the
reviewed manifest; missing return links fail CI (exit 1). Targets without
local manifest-listed HTML remain **unverified**, not confirmed healthy.
The action does not fetch other languages; by default, it does not enforce a
complete language cluster (a separate strict opt-in is described below).
Neither policy replaces the separate manual trusted-base manifest approval gate.

### Optional strict locale-cluster consistency

The pinned composite Action also supports an independent, default-off
`check-hreflang-clusters: 'true'` input. It compares the local hreflang
locale-to-URL map (including `x-default`) between linked, manifest-reviewed
HTML pages. Missing or conflicting local mappings and duplicate language labels
fail CI (exit 1); invalid input values exit 2. Outside-manifest destinations are
reported as **unverified**, not necessarily broken. Enable only for sites
deliberately publishing the same complete language set on every translated
page: partial reciprocal language links may be intentional, and Google does
not mandate complete locale clusters.

**Pin compatibility:** The copyable `uses:` example above already supports the strict locale-cluster input
at the independently merged full SHA. Enable it only after reviewing the site's
complete locale mapping and the pinned Action source. Older pins may silently
ignore this input. No URL is fetched, and separate manual trusted-base manifest
approval remains required.

### Optional internal-link nofollow policy

The Action source also provides a default-off
`check-internal-nofollow: 'true'` input that checks active `a` and `area`
links to **reviewed same-origin HTML** for `rel=nofollow`. When enabled,
matching links fail the offline check (exit 1), so teams can review accidental
internal link restrictions without contacting live URLs. External, unreviewed
and non-HTML targets are not asserted healthy by this check. Page-level
`robots` nofollow is a separate policy; neither is proof of live crawlability.
Invalid non-boolean input exits 2 without a reviewer success summary.

**Pin compatibility:** The copyable `uses:` pin above already supports the internal nofollow input.
Enable it only after reviewing intentional link restrictions and the pinned
Action source. Older pins can silently ignore unknown inputs. Manual trusted-base
manifest approval remains a separate required gate.

### Optional reviewed navigation and fragment integrity

The Action **source** also supports two independent default-off inputs:
`check-reviewed-navigation: 'true'` and `check-reviewed-fragments: 'true'`.
The first flags active same-origin links whose destinations are absent from the
developer-reviewed manifest; use it **only when the public route list is complete**.
Intentional redirects, rewrite rules, dynamic paths and query variants can
otherwise trigger failures. The second checks fragment IDs and named anchors on
**reviewed same-origin HTML** and flags missing or ambiguous targets. Unreviewed
routes, JavaScript-generated anchors, runtime navigation and deployed HTTP
responses are not verified. These opt-ins can be enabled separately, return exit
**1** for confirmed offline findings and exit **2** for invalid boolean values.
Neither check fetches URLs or replaces separate manual trusted-base manifest
approval.

**Pin compatibility:** The copyable example above already supports both reviewed navigation and fragment inputs
at the independently merged 40-character SHA. Enable either only after reviewing
the pinned Action and the site's route/anchor expectations; incomplete manifests
can create intentional failures. An older Action pin may silently ignore these
inputs. Neither check approves newly added manifest assertions or substitutes
for a separate trusted-base manual approval gate.

### Optional duplicate metadata regression gate

For reviewed same-origin **indexable** HTML pages, this Action revision adds
independent `check-unique-titles: 'true'` and
`check-unique-descriptions: 'true'` inputs. Either setting rejects duplicate
page titles or meta descriptions (exit 1); the default for each is `'false'`,
so sites can adopt the checks independently after reviewing intentional reuse.
Unreviewed built pages, other origins and reviewed `noindex` pages are outside
this duplicate check. This is an offline compiled-artifact comparison, not a
search-engine duplicate-content diagnosis.

**Pin compatibility:** The copyable `uses:` example above pins the independently
merged SHA and already supports these two inputs. Enable them only after the
site owner reviews intentional duplicate metadata and the exact Action source.
Older Action pins silently ignore unknown inputs, so recheck compatibility
when reusing this recipe. Keep the separate trusted-base manual manifest
approval gate; metadata checks do not make changing reviewed assertions safe.

### Optional inbound links to critical reviewed pages

The **pinned Action source** also supports `require-inbound-paths`, an optional
newline-separated list of up to 128 exact *manifest artifact paths*. For example,
the commented configuration in the copyable example can be enabled after the
site owner reviews the complete manifest and intended inbound-link policy:

```yaml
          require-inbound-paths: |
            pricing/index.html
            services/index.html
```

Each selected page must have at least one normal HTML link from **another**
reviewed same-origin page. Missing inbound links fail CI (exit 1); unknown,
duplicated, unsafe or blank listed paths fail as invalid input (exit 2).
The Action accepts paths containing spaces without shell interpretation. A final
YAML block-scalar newline is allowed, but empty lines inside the list are not.
Without this input, no inbound-link requirement is imposed. This is a chosen
critical-page policy, **not** a blanket claim that all unlinked pages harm SEO;
unreviewed, JavaScript-created and external links cannot prove a page is linked.

**Pin compatibility:** The copyable `uses:` revision above already supports the critical inbound
path input at the independently merged full SHA. Older revisions may silently
ignore this input; verify the pinned Action before enabling it. Manual trusted-base
manifest approval remains a separate gate;
no URL is fetched, customer workflow installed or site changed here.

### Optional source-linked SARIF hints

The **Action source** now offers a default-off `sarif-source-map` input. It
reuses the existing offline CLI's explicit source mapping when producing the
`seo-site.sarif` report, so a reviewed built HTML finding can point to a
corresponding checked-in Astro/Vite template instead of only the compiled
`dist/` artifact. For example, from the site repository root, commit and
review a separate `seo-source-map.json`:

```json
{
  "version": 1,
  "sources": {
    "index.html": "src/pages/index.astro"
  }
}
```

On an Action revision containing this input, optionally supply
`sarif-source-map: seo-source-map.json` under `with:`. Source paths must be
existing repository files, and artifact keys must name built HTML paths
from the supplied manifest or discovered build. A missing or invalid map is
an input error (exit **2**) with no trusted report bundle; omitting the input
preserves the ordinary compiled-artifact SARIF locations. The map is only a
reviewer-provided source *hint*: it cannot establish precise source line
provenance, source-map generation correctness, live page behavior or indexing.

**Pin compatibility:** The copyable `uses:` snippet above already supports the optional source-map input
at the independently merged full SHA. To retain source-linked SARIF for local
review, first commit a manually reviewed source map, uncomment the directory
creation step and both `report-directory` and `sarif-source-map` inputs.
Earlier pins may silently ignore unknown inputs. SARIF remains a local
create-only file in the optional report directory; nothing uploads it or
changes the separate manual manifest-approval gate.

### Optional portable reviewer reports

To keep the checker results for a later, separately reviewed CI step, create a
dedicated directory **outside the compiled build** before invoking the action:

```yaml
      - run: mkdir -p seo-ci-reports
```

Then add `report-directory: seo-ci-reports` under the existing action step's
`with:` inputs. The reviewed full SHA in the example above already supports
this input. Report retention remains opt-in and is disabled when the input is
omitted.

On a valid check (exit 0 or blocking SEO finding exit 1), that directory retains
four UTF-8, **create-only** files: `seo-site.json`, `seo-site.xml` (JUnit),
`seo-site.md`, and `seo-site.sarif`. An invalid manifest or report directory
exits 2; existing report filenames are never overwritten. The parent directory
must already exist, and the checker rejects report paths inside the compiled
build. Without this optional input, the action keeps its previous temporary
report behavior and still appends the same GitHub step summary. It does **not**
upload, publish, or authorize use of these files; an owner must separately review
any collection or retention step. A passing artifact does not approve changes to
the reviewed manifest: the trusted-base manual approval check remains separate.

**This does not replace manual approval integrity review.** The composite action
does not compare a PR's manifest with its trusted base, so changing approved
assertions in the candidate is outside its scope. For the stronger dual-gate
workflow, retain the full example below, including `manifest_review_cli` against
the immutable PR base and a separate built-site policy check. A passing action
does not approve a candidate manifest or establish live crawl/indexing results.

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
   whitespace-only lines and optionally indented `#` comments are ignored;
   non-comment build paths are never trimmed, so stray surrounding whitespace
   still fails validation). Then generate a **new** manifest:

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
   2026-11-19. The checker pin is the immutable merged PR #302 revision, which
   includes the unreviewed-path preview, `--unreviewed-file` queue export,
   `--only-file` curated selection, `--extend-from` incremental manifest
   updates, `manifest_review_cli` approval-integrity checks, and opt-in GitHub
   Actions approval error annotations and create-only Markdown reviewer
   reports. Keep manifest
   generation separate from CI and require deliberate human review before
   adoption. When changing the pin, verify supported flags and behavior.
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

## Protect previously approved assertions during pull requests

The inactive example first builds the proposed site, then checks out only
`seo-manifest.json` from the immutable **base commit** SHA supplied by the
GitHub pull-request event. This separate read-only, sparse checkout uses
`persist-credentials: false` and avoids fetching full Git history. The
`manifest_review_cli` gate rejects removed, reordered or altered previously
approved assertions; it permits only appended pages, still subject to human
review. Its bounded log summary identifies up to ten newly appended paths as
**unreviewed**, with a remaining count for larger batches, so reviewers can
inspect those exact additions in the PR diff before accepting them. A PASS from
this integrity gate does not approve new assertions or establish SEO health;
the independent `site_cli` gate checks compiled SEO behavior.

The copyable workflow enables `--github-annotations` and `--markdown-file`
for the approval step: approval drift or invalid input emits an escaped,
bounded error annotation in GitHub Actions. When the approval check exits 0
or 1, the job appends its bounded `seo-ci-reports/approval-review.md` to
`GITHUB_STEP_SUMMARY` while preserving the original exit code. The summary
distinguishes an intact approved prefix, newly appended **UNREVIEWED** pages,
and blocking changes; it never confers approval. Invalid input (exit 2)
creates no approval report and is not treated as successful verification.
This requires the pinned merged #302 checker or a separately verified newer
immutable revision. The workflow remains inactive in this repository.

The prior approved file must come from the trusted base commit, **not from the
same pull request** as the candidate manifest: comparing two PR-controlled
copies can incorrectly pass when both have weakened assertions. If the base
has no `seo-manifest.json`, this is **initial adoption**: the example emits a
notice, emits no approval summary, and leaves first-time approval to a human
reviewer. The approval-drift
step runs only for pull requests, not manual `workflow_dispatch` events, while
the compiled-site SEO gate still runs. This example does not auto-approve
manifests or prevent authorized workflow-policy changes.

For initial adoption with no approved base, the first-use job summary explicitly
states that **no approval-integrity comparison** ran and manual review is
required. It does not generate a fake approval report or change the job's exit
codes. Passing the compiled-site SEO gate alone does not approve the candidate
manifest; the reviewer must intentionally approve it before merging.

If the approval review fails, the independent SEO policy gate still runs
against the built candidate so reviewers can see both failure signals in the
same CI job. If the build or checkout fails, the SEO gate is skipped rather
than attempting to check missing artifacts; a cancelled job also does not
continue. Neither checker can turn the other's failure into a pass.

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

On **Windows PowerShell**, paste the following commands from the Searcharis
repository root. They run directly in the shell (no unsigned `.ps1` script or
execution-policy change) and check all three exit codes. The temporary report
directories remain available for inspection; nothing is deleted.

```powershell
$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $PWD.Path 'src'
try {
    $passReports = Join-Path ([System.IO.Path]::GetTempPath()) ('seo-pass-' + [guid]::NewGuid().ToString('N'))
    $brokenReports = Join-Path ([System.IO.Path]::GetTempPath()) ('seo-fail-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $passReports -ErrorAction Stop | Out-Null
    New-Item -ItemType Directory -Path $brokenReports -ErrorAction Stop | Out-Null

    python -S -m searcharis.site_cli examples/seo-contract/generator-fixtures/vite/manifest.json examples/seo-contract/generator-fixtures/vite/artifacts --check-canonical-consistency --report-dir $passReports --github-annotations
    if ($LASTEXITCODE -ne 0) { throw 'Healthy fixture did not return exit 0' }

    python -S -m searcharis.site_cli examples/seo-contract/regression-demo/manifest.json examples/seo-contract/regression-demo/candidate --check-canonical-consistency --report-dir $brokenReports --github-annotations
    if ($LASTEXITCODE -ne 1) { throw 'Broken fixture did not return exit 1' }

    python -S -m searcharis.site_cli examples/seo-contract/missing-manifest.json examples/seo-contract/generator-fixtures/vite/artifacts
    if ($LASTEXITCODE -ne 2) { throw 'Invalid input did not return exit 2' }

    # Rehearse the separate approval-integrity gate using only local fixtures.
    # The approved prefix is intentionally shorter than the candidate manifest.
    $fixtureManifest = 'examples/seo-contract/generator-fixtures/vite/manifest.json'
    $fixtureBuild = 'examples/seo-contract/generator-fixtures/vite/artifacts'
    $approvalDir = Join-Path ([System.IO.Path]::GetTempPath()) ('seo-approval-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $approvalDir -ErrorAction Stop | Out-Null
    $approvedFile = Join-Path $approvalDir 'approved.json'
    $weakenedFile = Join-Path $approvalDir 'weakened.json'
    $noBom = [System.Text.UTF8Encoding]::new($false)
    $fixture = Get-Content -LiteralPath $fixtureManifest -Raw | ConvertFrom-Json
    $approved = @{ version = 1; pages = @($fixture.pages[0]) } | ConvertTo-Json -Depth 16
    [System.IO.File]::WriteAllText($approvedFile, $approved, $noBom)

    python -S -m searcharis.manifest_review_cli $approvedFile $fixtureManifest $fixtureBuild
    if ($LASTEXITCODE -ne 0) { throw 'approved manifest review did not return exit 0' }

    $weakened = Get-Content -LiteralPath $fixtureManifest -Raw | ConvertFrom-Json
    $weakened.pages[0].contract.h1_min = 0
    [System.IO.File]::WriteAllText($weakenedFile, ($weakened | ConvertTo-Json -Depth 16), $noBom)
    python -S -m searcharis.manifest_review_cli $approvedFile $weakenedFile $fixtureBuild
    if ($LASTEXITCODE -ne 1) { throw 'weakened manifest review did not return exit 1' }

    python -S -m searcharis.manifest_review_cli $approvedFile (Join-Path $approvalDir 'missing.json') $fixtureBuild
    if ($LASTEXITCODE -ne 2) { throw 'invalid manifest review did not return exit 2' }

    foreach ($dir in @($passReports, $brokenReports)) {
        foreach ($name in @('seo-site.json', 'seo-site.md', 'seo-site.xml', 'seo-site.sarif')) {
            if (-not (Test-Path -LiteralPath (Join-Path $dir $name))) {
                throw "Expected report missing: $name in $dir"
            }
        }
    }
    Write-Output "PowerShell offline rehearsal PASS; reports: $passReports ; $brokenReports ; approval fixtures: $approvalDir"
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}
```

Each successful or regression run creates `seo-site.json`, `seo-site.md`,
`seo-site.xml`, and `seo-site.sarif` in its separate report directory.
The report directory **must exist before invocation** (hence `mktemp -d`
above and `mkdir -p` in the workflow). A missing manifest or unreadable
artifact exits 2 and does not create a trusted report bundle. The check
does not fetch URLs, and passing these fixture rehearsals does not prove
that another site's workflow has been installed or executed.

The same PowerShell rehearsal now also exercises the **separate approval
integrity gate**: an intact prefix with an appended, still-unreviewed page
passes (exit 0); weakening an approved assertion fails (exit 1); a missing
manifest fails as invalid input (exit 2). The temporary approval manifests are
created outside the repository without a UTF-8 BOM, remain available for
inspection, and never replace the site's approved manifest. A passing review
does not approve the appended page or replace the compiled-site SEO check.

The checker is launched with `python -S` and `PYTHONPATH` pointed at the pinned
source checkout. This specific offline `site_cli` entry point currently runs
on the Python standard library without installing Searcharis' cloud/demo
dependencies. This is deliberate; other Searcharis services and CLIs may have
different requirements. The sample downloads the site build dependencies and
GitHub Action/source checkouts in the normal way; the **SEO verifier itself**
never fetches URLs supplied in contracts.

The job grants only `contents: read`, does not persist checkout credentials,
has no secrets, and does not deploy or upload reports. It creates JSON, JUnit,
Markdown and SARIF files within `seo-ci-reports/` and appends separate bounded
approval-integrity and compiled-site Markdown summaries to `GITHUB_STEP_SUMMARY`
only after each respective checker exits 0 or 1. Original exit codes are
preserved; exit 2 does not produce a trusted summary. CI artifacts can contain
page URLs and findings: add
optional upload/retention and permissions only with separate owner review.

This workflow checks **compiled static HTML** and explicitly reviewed routes,
not dynamic DOM, effective server headers, live robots.txt, Google indexing,
rankings or customer usage. It must not be used to claim those outcomes.

For the complete input contract and additional sitemap/robots checks, see
[`../../../docs/CI-INTEGRATION.md`](../../../docs/CI-INTEGRATION.md).
