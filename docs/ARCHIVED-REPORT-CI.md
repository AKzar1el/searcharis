# Review archived SEO regressions in CI

Use this flow when a build stage has already saved two complete UTF-8
`searcharis.site_cli --json` reports. The baseline must come from a trusted,
independently built snapshot; the candidate must come from the new build. Both
reports should use the same reviewed manifest and cross-page check options.
The checker reads these local JSON files and does not fetch their declared URLs.

## Create a human review artifact

From the Searcharis repository, with Python 3.11+ and `uv` available:

```sh
# Create the output directory first; each report destination must be unused.
mkdir -p reports
uv run python -m searcharis.report_compare \
  artifacts/baseline.json artifacts/candidate.json \
  --markdown-file reports/seo-review.md
```

The `artifacts/` paths are example inputs, not bundled fixtures. Supply actual
saved reports from your own CI stages. `--markdown-file` writes a bounded,
escaped UTF-8 Markdown report, suitable for a human review or CI job summary.
It includes new and resolved issues, inherited debt, removed reviewed pages,
new unlisted HTML and unverified cross-page targets, showing whether optional
coverage/target gates block. The report contains no source-template guesses.

Exit **0** means no new blocking regressions; exit **1** means new blocking
regressions; exit **2** means malformed, incompatible or inaccessible input,
or an invalid output destination. **Preserve these exit codes in CI.** The
file is create-only: an existing file, an input file, a missing parent
folder or invalid input is never silently overwritten. It emits no report to
stdout in file mode. If a write is interrupted, discard any partial report.

For a complete, machine-readable diff, choose `--json` or the separate
`--json-file reports/seo-comparison.json` mode. The archived comparator's
`--json`, `--json-file` and `--markdown-file` modes are mutually exclusive;
run separate comparisons when both review and machine artifacts are needed.
Markdown intentionally limits detailed findings so unexpected page content
cannot flood a CI summary, and escapes HTML in untrusted input.

This is an **offline build-artifact comparison**, not a live crawl or proof of
Google indexing, search traffic, downstream CI consumption or customer adoption.
No upload, provider API, deployment, hosted GitHub Action, credentials or paid
service is required.

For the contract and report formats, see [SEO contract CI](SEO-CONTRACT-CI.md).
For the passing and failing starter example, see [CI integration](CI-INTEGRATION.md).
