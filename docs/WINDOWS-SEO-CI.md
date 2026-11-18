# Windows PowerShell offline SEO CI quickstart

This is a **copy/paste PowerShell 5.1 or PowerShell 7** entrypoint for an
already-built static site. It does not need Bash, redirect JSON through the
shell, fetch public URLs, modify a workflow, or deploy anything. Run it from the
Searcharis repository root with Python 3.11+ and `uv` installed.

The checked-in Vite fixture is a **compiled two-page snapshot**, not a customer
deployment. Its manifest is intentionally reviewed and versioned.

## Run one passing site gate

```powershell
$manifest = 'examples/seo-contract/generator-fixtures/vite/manifest.json'
$build = 'examples/seo-contract/generator-fixtures/vite/artifacts'
$reports = Join-Path $env:TEMP ('sear-SEO-' + [guid]::NewGuid().ToString('N'))
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'uv is required' }
New-Item -ItemType Directory -Path $reports -ErrorAction Stop | Out-Null

& uv run python -m searcharis.site_cli $manifest $build `
    --require-full-coverage --check-canonical-consistency --report-dir $reports
$gateExit = $LASTEXITCODE
Write-Host "SEO gate exit: $gateExit; reports: $reports"
```

The expected `$gateExit` is **0**. The one-pass, create-only bundle contains
`seo-site.json` (complete findings), `seo-site.xml` (JUnit XML),
`seo-site.md` (bounded reviewer summary), and `seo-site.sarif` (SARIF 2.1.0).
These are UTF-8 files, not PowerShell 5.1 `>` redirections, which can create
UTF-16LE text incompatible with downstream CI consumers.

Inspect the reports without altering the checker result:

```powershell
Get-ChildItem -LiteralPath $reports -File | Select-Object Name,Length
$report = Get-Content -LiteralPath (Join-Path $reports 'seo-site.json') -Raw -Encoding UTF8 | ConvertFrom-Json
[xml]$junit = Get-Content -LiteralPath (Join-Path $reports 'seo-site.xml') -Raw -Encoding UTF8
Write-Host "JSON status: $($report.status); JUnit root: $($junit.DocumentElement.LocalName)"
```

The output directory **must already exist**, and the four output filenames
must not exist. Each example uses a unique directory so reruns do not overwrite
or mix reports. Keep it outside the built site and use your CI provider's
**separately configured** artifact and JUnit/SARIF collection. Report creation
itself uploads nothing.

## Prove that a regression blocks CI

The checked-in candidate below has a new, blocking pricing-page title defect.
Run the same gate against that deliberately broken snapshot:

```powershell
$manifest = 'examples/seo-contract/regression-demo/manifest.json'
$build = 'examples/seo-contract/regression-demo/candidate'
$reports = Join-Path $env:TEMP ('sear-SEO-fail-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $reports -ErrorAction Stop | Out-Null

& uv run python -m searcharis.site_cli $manifest $build `
    --require-full-coverage --report-dir $reports
$gateExit = $LASTEXITCODE
Write-Host "Expected blocking exit 1; observed: $gateExit; reports: $reports"
```

Expected: **exit 1**, with the same four parseable report files retained for
review. A missing/invalid manifest instead returns **exit 2**. On exit 2,
reports may be absent or incomplete; never treat them as valid evidence.
The checker never requests the manifest's public `url` values.

## Preserve the exit status in an isolated CI step

In a dedicated PowerShell CI step, capture the CLI's native exit code
**immediately** after invocation, then collect artifacts in separately
configured provider steps that run even when the gate exits 1:

```powershell
& uv run python -m searcharis.site_cli $manifest $build --require-full-coverage --report-dir $reports
$gateExit = $LASTEXITCODE
exit $gateExit
```

`exit` intentionally terminates the hosting PowerShell process: use it in
an **isolated CI step**, not when pasting commands into an interactive shell
you want to keep open. Do not replace it with a generic `throw` if your
pipeline needs to retain the distinction between policy failure (1) and
invalid input/output (2). Validate `uv`, manifest/build paths, and the fresh
report directory before invoking the native command; missing `uv` is a
PowerShell invocation error, not a checker exit code.

For an actual Astro/Vite site, generate the static build with its own build
command, review and commit an explicit manifest once, and replace the two
paths above. Do **not** regenerate assertions on each CI run. Add cross-page,
sitemap, robots and indexability gates only after reviewing their assumptions.
See [CI integration](CI-INTEGRATION.md) and
[contract semantics](SEO-CONTRACT-CI.md).

This fixture check does not measure actual search indexing, hosted routing,
customer adoption or commercial benefit.
